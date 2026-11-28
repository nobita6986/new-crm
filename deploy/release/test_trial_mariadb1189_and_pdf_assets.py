#!/usr/bin/env python3
"""Unit & contract tests for MariaDB 11.8.9, PDF SSRF/file/RBAC lockdown, inventory & provenance."""

from __future__ import annotations

import ast
import base64
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bs4  # Hard requirement (`F-TRIAL-INV-01`): never skip silently if bs4 is missing!

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_pdf_assets_with_stub_frappe(
    sites_dir: Path,
    site_name: str,
    *,
    user: str = "Administrator",
    files_db: dict[str, dict[str, object]] | None = None,
    perm_callback=None,
):
    """Load `crm_cskh.services.pdf_hooks` + `pdf_assets` against a deterministic stub `frappe` runtime."""
    assert bs4.__version__
    records = files_db if files_db is not None else {}

    stub_frappe = types.ModuleType("frappe")
    stub_local = types.SimpleNamespace(
        sites_path=str(sites_dir),
        site=site_name,
        request=types.SimpleNamespace(host=site_name),
    )
    stub_frappe.local = stub_local  # type: ignore[attr-defined]
    stub_frappe.session = types.SimpleNamespace(user=user, sid="secret-sid-123")  # type: ignore[attr-defined]

    stub_utils = types.ModuleType("frappe.utils")
    stub_utils.get_url = lambda: f"http://{site_name}"  # type: ignore[attr-defined]
    stub_frappe.utils = stub_utils  # type: ignore[attr-defined]
    stub_frappe.get_site_path = lambda *parts: str(sites_dir / site_name / Path(*parts))  # type: ignore[attr-defined]

    def _get_all(doctype: str, filters=None, pluck=None, **_kw):
        if doctype != "File":
            return []
        fdict = filters or {}
        matched = []
        for fname, meta in records.items():
            if fdict.get("file_url") and meta.get("file_url") != fdict["file_url"]:
                continue
            if "is_private" in fdict and int(meta.get("is_private", 0)) != int(
                fdict["is_private"]
            ):
                continue
            if fdict.get("name") and fname != fdict["name"]:
                continue
            matched.append(fname if pluck == "name" else {"name": fname, **meta})
        return matched

    def _get_doc(doctype: str, name: str):
        assert doctype == "File"
        meta = records[name]
        return types.SimpleNamespace(doctype="File", name=name, **meta)

    def _has_permission(doctype: str, ptype: str = "read", doc=None, user=None, **_kw):
        eff_user = user or stub_frappe.session.user
        if perm_callback is not None:
            return bool(perm_callback(doctype, ptype, doc, eff_user))
        return eff_user == "Administrator"

    stub_frappe.get_all = _get_all  # type: ignore[attr-defined]
    stub_frappe.get_doc = _get_doc  # type: ignore[attr-defined]
    stub_frappe.has_permission = _has_permission  # type: ignore[attr-defined]

    saved_keys = (
        "frappe",
        "frappe.utils",
        "crm_cskh",
        "crm_cskh.services",
        "crm_cskh.services.pdf_hooks",
        "crm_cskh.services.pdf_assets",
    )
    saved: dict[str, object] = {k: sys.modules.get(k) for k in saved_keys}
    saved["__meta_path__"] = list(sys.meta_path)
    sys.modules["frappe"] = stub_frappe
    sys.modules["frappe.utils"] = stub_utils
    try:
        cskh_pkg = types.ModuleType("crm_cskh")
        cskh_pkg.__path__ = [str(REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh")]  # type: ignore[attr-defined]
        services_pkg = types.ModuleType("crm_cskh.services")
        services_pkg.__path__ = [  # type: ignore[attr-defined]
            str(REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "services")
        ]
        sys.modules["crm_cskh"] = cskh_pkg
        sys.modules["crm_cskh.services"] = services_pkg

        hooks_path = (
            REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "services" / "pdf_hooks.py"
        )
        hooks_spec = importlib.util.spec_from_file_location(
            "crm_cskh.services.pdf_hooks", hooks_path
        )
        assert hooks_spec and hooks_spec.loader
        hooks_mod = importlib.util.module_from_spec(hooks_spec)
        sys.modules["crm_cskh.services.pdf_hooks"] = hooks_mod
        hooks_spec.loader.exec_module(hooks_mod)
        services_pkg.pdf_hooks = hooks_mod  # type: ignore[attr-defined]

        mod_path = (
            REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "services" / "pdf_assets.py"
        )
        spec = importlib.util.spec_from_file_location(
            "crm_cskh.services.pdf_assets", mod_path
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["crm_cskh.services.pdf_assets"] = mod
        spec.loader.exec_module(mod)
        services_pkg.pdf_assets = mod  # type: ignore[attr-defined]
        return mod, stub_frappe, saved
    except Exception:
        _restore_modules(saved)
        raise


def _restore_modules(saved: dict[str, object]) -> None:
    orig_mp = saved.get("__meta_path__")
    if isinstance(orig_mp, list):
        sys.meta_path[:] = orig_mp
    for k, v in saved.items():
        if k == "__meta_path__":
            continue
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v  # type: ignore[assignment]


class TestTrialMariaDb1189AndPdfAssets(unittest.TestCase):
    def test_release_lock_containerfile_and_build_script_mariadb1189_and_tree_sha_sync(
        self,
    ) -> None:
        lock = json.loads(
            (REPO_ROOT / "deploy" / "images" / "release-lock.json").read_text(
                encoding="utf-8"
            )
        )
        cfile = (REPO_ROOT / "deploy" / "images" / "Containerfile").read_text(
            encoding="utf-8"
        )
        build_sh = (REPO_ROOT / "deploy" / "images" / "build-image.sh").read_text(
            encoding="utf-8"
        )
        exc_doc = json.loads(
            (REPO_ROOT / "deploy" / "release" / "cve-exceptions.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            lock["p4_01_baseline_lock_sha256"],
            "dde3bdaa11180cd103f8d8b064e20411d380135ac88d4a39226b7d451a990962",
        )
        self.assertFalse(lock["toolchain"]["install_chromium"])
        self.assertEqual(
            lock["security_patches"]["mariadb_client_debian_version"],
            "1:11.8.9+ds-0+deb13u1",
        )

        # F-TRIAL-PROV-01: All 5 MariaDB SHA-256s in release-lock, Containerfile, and build-image.sh --build-arg
        sha_map = lock["security_patches"]["mariadb_client_amd64_sha256"]
        expected_arg_map = {
            "mariadb-common": "MARIADB_COMMON_DEB_SHA256",
            "libmariadb3": "LIBMARIADB3_AMD64_DEB_SHA256",
            "mariadb-client-core": "MARIADB_CLIENT_CORE_AMD64_DEB_SHA256",
            "mariadb-client": "MARIADB_CLIENT_AMD64_DEB_SHA256",
            "libmariadb-dev": "LIBMARIADB_DEV_AMD64_DEB_SHA256",
        }
        self.assertEqual(set(sha_map), set(expected_arg_map))
        for pkg, arg_name in expected_arg_map.items():
            sha_hex = sha_map[pkg]
            self.assertEqual(len(sha_hex), 64, pkg)
            self.assertIn(f"ARG {arg_name}={sha_hex}", cfile)
            self.assertIn(f'--build-arg "{arg_name}=${{{arg_name}}}"', build_sh)

        # F-TRIAL-PROV-01: All tree SHA fields synchronized across release-lock & Containerfile
        self.assertEqual(
            lock["apps"]["crm_cskh"]["apps_tree_sha"], lock["apps_tree_sha"]
        )
        self.assertEqual(
            lock["apps"]["crm_cskh"]["crm_cskh_tree_sha"], lock["crm_cskh_tree_sha"]
        )
        cfile_apps_trees = re.findall(
            r"(?m)^ARG\s+APPS_TREE_SHA=([0-9a-f]{40})\s*$", cfile
        )
        cfile_cskh_trees = re.findall(
            r"(?m)^ARG\s+CRM_CSKH_TREE_SHA=([0-9a-f]{40})\s*$", cfile
        )
        self.assertEqual(cfile_apps_trees, [lock["apps_tree_sha"]] * 2)
        self.assertEqual(cfile_cskh_trees, [lock["crm_cskh_tree_sha"]] * 2)

        self.assertIn("ARG INSTALL_CHROMIUM=false", cfile)
        self.assertIn("ARG MARIADB_DEB_VERSION=1:11.8.9+ds-0+deb13u1", cfile)
        self.assertIn(
            "Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp", cfile
        )
        self.assertNotIn("proposed-updates", cfile.lower())
        self.assertNotIn("proposed-updates", json.dumps(lock).lower())
        self.assertIn(
            "debian-stable trixie/main",
            lock["security_patches"]["mariadb_client_debian_source"],
        )
        self.assertEqual(exc_doc.get("exceptions"), [])

        # AST-parse every inline python -c '...' script in Containerfile
        py_c_matches = re.findall(r"python3?\s+-c\s+'([^']+)'", cfile)
        self.assertGreaterEqual(len(py_c_matches), 4)
        for idx, snippet in enumerate(py_c_matches):
            ast.parse(snippet, filename=f"Containerfile:python_c_{idx}")

    def test_canonical_p3_inventory_and_ci_workflows_eliminate_bs4_skip(self) -> None:
        this_src = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn("skip" + "Test(", this_src)
        self.assertNotIn("@unittest." + "skip", this_src)

        for wf_rel in (
            ".github/workflows/p4-ci.yml",
            ".github/workflows/p4-candidate-verify.yml",
            ".github/workflows/p4-batch2-delta.yml",
        ):
            wf_txt = (REPO_ROOT / wf_rel).read_text(encoding="utf-8")
            self.assertIn("beautifulsoup4==4.12.3", wf_txt, wf_rel)

        p3_inv = json.loads(
            (
                REPO_ROOT / "deploy" / "dev" / "seed" / "p3_test_inventory.json"
            ).read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(int(p3_inv["expected_total"]), 53)
        pdf_mod_methods = p3_inv["modules"].get(
            "crm_cskh.tests.test_p3_pdf_security_and_worker", []
        )
        self.assertGreaterEqual(len(pdf_mod_methods), 8)

    def test_release_signing_secret_name_and_existing_digest_binding_sync(self) -> None:
        cand_wf = (
            REPO_ROOT / ".github" / "workflows" / "p4-candidate-verify.yml"
        ).read_text(encoding="utf-8")
        rel_wf = (REPO_ROOT / ".github" / "workflows" / "p4-release.yml").read_text(
            encoding="utf-8"
        )
        legacy_wrong_name = "CSKH_" + "RELEASE_SIGNING_PRIVATE_KEY"
        for wf_path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            self.assertNotIn(
                legacy_wrong_name,
                wf_path.read_text(encoding="utf-8"),
                wf_path.name,
            )
        self.assertIn("RELEASE_SIGNING_PRIVATE_KEY", cand_wf)
        self.assertIn("RELEASE_SIGNING_PRIVATE_KEY", rel_wf)
        self.assertIn("existing_app_image_digest", rel_wf)
        self.assertIn("deploy_to_staging", rel_wf)

    def test_all_embedded_python_probes_in_clean_site_and_smoke_compile_cleanly(
        self,
    ) -> None:
        dummy_ns = {
            "args": types.SimpleNamespace(site_name="p4-clean-verify.localhost"),
            "probe_vi_org": "Đối tác Nhân sự",
            "bkp_stage": {"lead_name": "CRM-LEAD-0001"},
        }
        for rel in (
            "deploy/images/verify_clean_site.py",
            "deploy/compose/scripts/run_isolated_smoke.py",
        ):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            mod_ast = ast.parse(src, filename=rel)
            checked = 0
            for node in ast.walk(mod_ast):
                if not isinstance(node, ast.Assign):
                    continue
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.endswith("_py"):
                        expr_code = compile(
                            ast.Expression(node.value), filename=rel, mode="eval"
                        )
                        rendered = eval(
                            expr_code, {"__builtins__": __builtins__}, dummy_ns
                        )
                        self.assertIsInstance(rendered, str)
                        ast.parse(rendered, filename=f"{rel}:{tgt.id}")
                        checked += 1
            self.assertGreaterEqual(checked, 3, rel)

    def test_pdf_assets_html_css_ssrf_protocol_relative_traversal_and_symlink_lockdown(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites = root / "sites"
            assets_dir = sites / "assets" / "frappe" / "dist" / "css"
            public_files = sites / "p4-smoke.localhost" / "public" / "files"
            assets_dir.mkdir(parents=True)
            public_files.mkdir(parents=True)

            font_bytes = b"FAKE-WOFF2-BYTES"
            (assets_dir / "font.woff2").write_bytes(font_bytes)
            (assets_dir / "print.bundle.123.css").write_text(
                '@import "https://evil.example.invalid/steal.css"; '
                '@font-face { src: url("font.woff2"); } '
                '.ssrf { background: url("http://127.0.0.1:6379/ping"); }',
                encoding="utf-8",
            )
            png_bytes = b"\x89PNG\r\n\x1a\nFAKEPNG"
            (public_files / "badge.png").write_bytes(png_bytes)
            secret_file = root / "secret.txt"
            secret_file.write_text("TOP_SECRET", encoding="utf-8")

            symlink_created = False
            sym_path = public_files / "sym.png"
            try:
                os.symlink(secret_file, sym_path)
                symlink_created = True
            except OSError:
                symlink_created = False

            mod, _, saved = _load_pdf_assets_with_stub_frappe(
                sites, "p4-smoke.localhost"
            )
            try:
                raw_html = (
                    "<html><head>"
                    '<base href="https://evil.example.invalid/">'
                    '<meta http-equiv="refresh" content="0;url=http://169.254.169.254/">'
                    '<link type="text/css" rel="stylesheet" '
                    'href="http://p4-smoke.localhost/assets/frappe/dist/css/print.bundle.123.css">'
                    '<link rel="stylesheet" href="https://evil.example.invalid/ext.css">'
                    '<style>@import "//evil.example.invalid/proto.css"; '
                    'div { background-image: url("file:///etc/passwd"); }</style>'
                    "</head><body style=\"background: url('https://evil.example.invalid/bg.png')\">"
                    '<img src="http://p4-smoke.localhost/files/badge.png"/>'
                    '<img src="http://p4-smoke.localhost/files/../../../secret.txt"/>'
                    '<img src="http://p4-smoke.localhost/files/%2e%2e/%2e%2e/%2e%2e/secret.txt"/>'
                    '<img src="//evil.example.invalid/files/badge.png"/>'
                    '<img src="//p4-smoke.localhost/files/badge.png"/>'
                    '<img src="\\\\p4-smoke.localhost\\files\\badge.png"/>'
                    '<img src="https://evil.example.invalid/ssrf.png"/>'
                    '<iframe src="http://127.0.0.1:8000/"></iframe>'
                    '<script src="https://evil.example.invalid/x.js"></script>'
                    "</body></html>"
                )
                out = mod.inline_local_pdf_assets(raw_html)
                self.assertIn(base64.b64encode(font_bytes).decode("ascii"), out)
                # Authorized badge appears ONCE (protocol-relative //p4-smoke.localhost/files/badge.png was blocked!)
                expected_badge_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
                self.assertEqual(out.count(expected_badge_uri), 1)
                for forbidden_tok in (
                    "evil.example.invalid",
                    "169.254.169.254",
                    "127.0.0.1:6379",
                    "127.0.0.1:8000",
                    "file:///etc/passwd",
                    "TOP_SECRET",
                    base64.b64encode(b"TOP_SECRET").decode("ascii"),
                    "<iframe",
                    "<script",
                    "<base",
                    "http-equiv",
                    "@import",
                ):
                    self.assertNotIn(forbidden_tok, out)

                if symlink_created:
                    self.assertIsNone(
                        mod._resolve_same_site_resource(
                            "/files/sym.png", include_private_files=True
                        )
                    )

                # SVG sanitization: strip <script> and external <image href=...> while rejecting <!ENTITY>
                xxe_svg = b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
                self.assertIsNone(mod._sanitize_svg_bytes(xxe_svg))
                dirty_svg = (
                    b'<svg xmlns="http://www.w3.org/2000/svg">'
                    b"<script>alert(1)</script>"
                    b'<image href="https://evil.example.invalid/spy.png"/>'
                    b'<rect width="10" height="10"/></svg>'
                )
                clean_svg = mod._sanitize_svg_bytes(dirty_svg)
                assert clean_svg is not None
                self.assertNotIn(b"script", clean_svg)
                self.assertNotIn(b"evil.example.invalid", clean_svg)
            finally:
                _restore_modules(saved)

    def test_private_file_rbac_weasyprint_html_css_and_multithreaded_hooks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites = root / "sites"
            site_dir = sites / "p4-smoke.localhost"
            (sites / "assets").mkdir(parents=True)
            priv_dir = site_dir / "private" / "files"
            priv_dir.mkdir(parents=True)

            priv_png = b"\x89PNG\r\n\x1a\nPRIVATEPNG"
            (priv_dir / "lead_secret.png").write_bytes(priv_png)

            lead_owner_state = {"LEAD-1": "staff.a@cskh.local"}
            files_db = {
                "FILE-PRIV-1": {
                    "file_url": "/private/files/lead_secret.png",
                    "is_private": 1,
                    "owner": "staff.a@cskh.local",
                    "attached_to_doctype": "CRM Lead",
                    "attached_to_name": "LEAD-1",
                }
            }

            def _perm_cb(doctype: str, _ptype: str, doc, eff_user: str) -> bool:
                if eff_user == "Administrator":
                    return True
                if doctype == "CRM Lead":
                    lead_id = doc if isinstance(doc, str) else getattr(doc, "name", "")
                    return lead_owner_state.get(lead_id) == eff_user
                if doctype == "File":
                    att_dt = getattr(doc, "attached_to_doctype", None)
                    att_dn = getattr(doc, "attached_to_name", None)
                    if att_dt != "CRM Lead" or not att_dn:
                        return False
                    return lead_owner_state.get(att_dn) == eff_user
                return False

            mod, stub_frappe, saved = _load_pdf_assets_with_stub_frappe(
                sites,
                "p4-smoke.localhost",
                user="Guest",
                files_db=files_db,
                perm_callback=_perm_cb,
            )
            try:
                # 1. Guest -> denied
                self.assertIsNone(
                    mod._resolve_same_site_resource(
                        "/private/files/lead_secret.png", include_private_files=True
                    )
                )
                # 2. Out-of-scope staff.b -> denied
                stub_frappe.session.user = "staff.b@cskh.local"
                self.assertIsNone(
                    mod._resolve_same_site_resource(
                        "/private/files/lead_secret.png", include_private_files=True
                    )
                )
                # 3. Current Lead owner staff.a -> allowed
                stub_frappe.session.user = "staff.a@cskh.local"
                hit_a = mod._resolve_same_site_resource(
                    "/private/files/lead_secret.png", include_private_files=True
                )
                self.assertIsNotNone(hit_a)
                assert hit_a is not None
                self.assertEqual(hit_a[0], priv_png)
                self.assertIsNotNone(
                    mod._guarded_frappe_get_base64_image(
                        "/private/files/lead_secret.png"
                    )
                )

                # 4. Reassign CRM Lead to staff.b -> former owner staff.a (still file.owner!) is DENIED
                lead_owner_state["LEAD-1"] = "staff.b@cskh.local"
                self.assertIsNone(
                    mod._resolve_same_site_resource(
                        "/private/files/lead_secret.png", include_private_files=True
                    )
                )
                self.assertIsNone(
                    mod._guarded_frappe_get_base64_image(
                        "/private/files/lead_secret.png"
                    )
                )
                stub_frappe.session.user = "staff.b@cskh.local"
                self.assertIsNotNone(
                    mod._resolve_same_site_resource(
                        "/private/files/lead_secret.png", include_private_files=True
                    )
                )

                # 5. wkhtmltopdf options sanitizer forces proxy=127.0.0.1:1, disable-local-file-access & strips cookie-jar
                self.assertEqual(mod._guarded_frappe_get_cookie_options(), {})
                s_opts = mod._sanitize_wkhtmltopdf_options(
                    {"cookie-jar": "/nonexistent.jar", "allow": "/etc"}
                )
                self.assertNotIn("cookie-jar", s_opts)
                self.assertNotIn("allow", s_opts)
                self.assertEqual(s_opts["proxy"], "http://127.0.0.1:1")
                self.assertEqual(s_opts["disable-local-file-access"], "")
                self.assertEqual(s_opts["disable-javascript"], "")

                # 6. WeasyPrint 70+ URLFetcher + HTML & CSS factory lockdown + 16-thread hook init
                wp_mod = types.ModuleType("weasyprint")
                wp_urls_mod = types.ModuleType("weasyprint.urls")

                class _FakeURLFetcher:
                    def __init__(
                        self,
                        fail_on_errors=False,
                        allow_redirects=True,
                        allowed_protocols=None,
                    ):
                        self.fail_on_errors = fail_on_errors
                        self.allow_redirects = allow_redirects
                        self.allowed_protocols = allowed_protocols

                    def fetch(self, url, headers=None):
                        return ("SUPER_FETCH", url)

                class _FakeURLFetcherResponse:
                    def __init__(self, url, body=None, headers=None, status=200):
                        self.url = url
                        self.body = body
                        self.headers = headers or {}
                        self.status = status

                class _RawHTML:
                    def __init__(self, **kwargs):
                        self.kwargs = kwargs

                class _RawCSS:
                    def __init__(self, **kwargs):
                        self.kwargs = kwargs

                wp_mod.HTML = _RawHTML  # type: ignore[attr-defined]
                wp_mod.CSS = _RawCSS  # type: ignore[attr-defined]
                wp_urls_mod.URLFetcher = _FakeURLFetcher  # type: ignore[attr-defined]
                wp_urls_mod.URLFetcherResponse = _FakeURLFetcherResponse  # type: ignore[attr-defined]

                frappe_pdf_mod = types.ModuleType("frappe.utils.pdf")
                frappe_pdf_mod.pdfkit = types.SimpleNamespace(  # type: ignore[attr-defined]
                    from_string=lambda h, *a, **kw: (h, a, kw),
                    from_url=lambda *_a, **_kw: b"",
                    from_file=lambda *_a, **_kw: b"",
                )
                frappe_pdf_mod._get_base64_image = lambda _s: None  # type: ignore[attr-defined]
                frappe_pdf_mod.inline_private_images = lambda h: h  # type: ignore[attr-defined]
                frappe_pdf_mod.get_cookie_options = lambda: {"cookie-jar": "/tmp/x.jar"}  # type: ignore[attr-defined]

                frappe_wp_mod = types.ModuleType("frappe.utils.weasyprint")
                frappe_wp_mod.PrintFormatGenerator = type(  # type: ignore[attr-defined]
                    "PrintFormatGenerator",
                    (),
                    {"get_element": staticmethod(lambda b, e: None)},
                )
                frappe_wp_mod.import_weasyprint = lambda: (_RawHTML, _RawCSS)  # type: ignore[attr-defined]

                extra_saved = {
                    k: sys.modules.get(k)
                    for k in (
                        "weasyprint",
                        "weasyprint.urls",
                        "frappe.utils.pdf",
                        "frappe.utils.weasyprint",
                    )
                }
                sys.modules["weasyprint"] = wp_mod
                sys.modules["weasyprint.urls"] = wp_urls_mod
                sys.modules["frappe.utils.pdf"] = frappe_pdf_mod
                sys.modules["frappe.utils.weasyprint"] = frappe_wp_mod
                try:
                    barrier = threading.Barrier(16)

                    def _init_worker(_i: int) -> bool:
                        barrier.wait(timeout=10)
                        mod.ensure_pdf_runtime_hooks()
                        return bool(mod._HOOKS_INSTALLED)

                    with ThreadPoolExecutor(max_workers=16) as pool:
                        self.assertEqual(
                            list(pool.map(_init_worker, range(16))), [True] * 16
                        )
                    self.assertEqual(mod._WRAP_INSTALL_COUNT, 1)

                    html_cls, css_cls = frappe_wp_mod.import_weasyprint()
                    for blocked in (
                        lambda: html_cls("/etc/passwd"),
                        lambda: html_cls(filename="/etc/passwd"),
                        lambda: html_cls(file_obj=io.StringIO("<p>x</p>")),
                        lambda: html_cls(url="https://evil.example.invalid/"),
                        lambda: css_cls("/etc/passwd"),
                        lambda: css_cls(filename="/etc/passwd"),
                        lambda: css_cls(file_obj=io.StringIO("body{}")),
                        lambda: css_cls(url="https://evil.example.invalid/a.css"),
                    ):
                        with self.assertRaises(ValueError):
                            blocked()
                    with self.assertRaises(PermissionError):
                        frappe_pdf_mod.pdfkit.from_url("https://evil.example.invalid/")
                    with self.assertRaises(PermissionError):
                        frappe_pdf_mod.pdfkit.from_file("/etc/passwd")

                    h_inst = html_cls(
                        string='<img src="https://evil.example.invalid/x.png"/>',
                        base_url="/etc/",
                        url_fetcher="CALLER_BYPASS_ATTEMPT",
                    )
                    c_inst = css_cls(
                        string='@import "//evil.example.invalid/x.css"; body { background: url("file:///etc/passwd"); }',
                        base_url="file:///etc/",
                        url_fetcher="CALLER_BYPASS_ATTEMPT",
                    )
                    self.assertEqual(
                        h_inst.kwargs["base_url"], "http://p4-smoke.localhost/"
                    )
                    self.assertEqual(
                        c_inst.kwargs["base_url"], "http://p4-smoke.localhost/"
                    )
                    self.assertIsInstance(h_inst.kwargs["url_fetcher"], _FakeURLFetcher)
                    self.assertIsInstance(c_inst.kwargs["url_fetcher"], _FakeURLFetcher)
                    self.assertFalse(c_inst.kwargs["url_fetcher"].allow_redirects)
                    self.assertEqual(
                        c_inst.kwargs["url_fetcher"].allowed_protocols, {"data"}
                    )
                    self.assertNotIn("evil.example.invalid", h_inst.kwargs["string"])
                    self.assertNotIn("evil.example.invalid", c_inst.kwargs["string"])
                    self.assertNotIn("file:///etc/passwd", c_inst.kwargs["string"])
                finally:
                    _restore_modules(extra_saved)
            finally:
                _restore_modules(saved)

    def test_lazy_pdf_hooks_zero_eager_imports_and_pre_first_use_meta_path_interception(
        self,
    ) -> None:
        """Verify `pdf_hooks` has near-zero import/call cost and patches engines before first import returns."""
        hooks_src = (
            REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "hooks.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'before_job = ["crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks"]',
            hooks_src,
        )
        self.assertIn(
            '"crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks"',
            hooks_src,
        )
        self.assertIn(
            'on_print_pdf = ["crm_cskh.services.pdf_hooks.on_print_pdf"]',
            hooks_src,
        )

        # Spawn a real clean child Python process to verify zero eager imports & pre-first-use loader patching
        with tempfile.TemporaryDirectory() as td:
            fake_sp = Path(td) / "fake_site_packages"
            (fake_sp / "weasyprint").mkdir(parents=True)
            (fake_sp / "weasyprint" / "urls.py").write_text(
                "class URLFetcher:\n"
                "    def __init__(self, fail_on_errors=False, allow_redirects=True, allowed_protocols=None):\n"
                "        self.allow_redirects = allow_redirects\n"
                "        self.allowed_protocols = allowed_protocols\n"
                "class URLFetcherResponse:\n"
                "    def __init__(self, url, body=None, headers=None, status=200):\n"
                "        self.url = url\n"
                "        self.body = body\n",
                encoding="utf-8",
            )
            (fake_sp / "weasyprint" / "__init__.py").write_text(
                "class HTML:\n"
                "    def __init__(self, **kwargs):\n"
                "        self.kwargs = kwargs\n"
                "class CSS:\n"
                "    def __init__(self, **kwargs):\n"
                "        self.kwargs = kwargs\n",
                encoding="utf-8",
            )
            (fake_sp / "pdfkit").mkdir(parents=True)
            (fake_sp / "pdfkit" / "__init__.py").write_text(
                "def from_string(html, *a, **kw):\n"
                "    return (html, a, kw)\n"
                "def from_url(*a, **kw):\n"
                "    return b'UNSAFE'\n"
                "def from_file(*a, **kw):\n"
                "    return b'UNSAFE'\n",
                encoding="utf-8",
            )
            (fake_sp / "frappe" / "utils").mkdir(parents=True)
            (fake_sp / "frappe" / "__init__.py").write_text(
                "import types\n"
                "local = types.SimpleNamespace(sites_path='.', site='lazy.localhost', request=None)\n"
                "session = types.SimpleNamespace(user='Administrator', sid='sid')\n"
                "get_site_path = lambda *p: '/'.join(('sites', 'lazy.localhost', *p))\n"
                "get_all = lambda *a, **k: []\n"
                "has_permission = lambda *a, **k: True\n",
                encoding="utf-8",
            )
            (fake_sp / "frappe" / "utils" / "__init__.py").write_text(
                "get_url = lambda: 'http://lazy.localhost'\n",
                encoding="utf-8",
            )
            (fake_sp / "frappe" / "utils" / "pdf.py").write_text(
                "import pdfkit\n"
                "_get_base64_image = lambda s: 'UNPATCHED'\n"
                "inline_private_images = lambda h: h\n"
                "get_cookie_options = lambda: {'cookie-jar': '/tmp/bad.jar'}\n",
                encoding="utf-8",
            )

            sub_py = r"""
import importlib, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

t0 = time.perf_counter()
pdf_hooks = importlib.import_module("crm_cskh.services.pdf_hooks")
t1 = time.perf_counter()
pdf_hooks.ensure_pdf_runtime_hooks()
t2 = time.perf_counter()

eager_after_hook = {
    m: (m in sys.modules)
    for m in (
        "weasyprint",
        "pdfkit",
        "frappe.utils.pdf",
        "frappe.utils.weasyprint",
        "bs4",
        "xml.etree.ElementTree",
        "crm_cskh.services.pdf_assets",
    )
}

# Verify `from weasyprint import HTML, CSS` binds ALREADY-PATCHED classes on very first import across 8 threads!
barrier = threading.Barrier(8)
def _race_import(i: int):
    barrier.wait(timeout=10)
    if i % 2 == 0:
        from weasyprint import CSS, HTML
        return bool(getattr(HTML, "_cskh_pdf_wrapped", False) and getattr(CSS, "_cskh_pdf_wrapped", False))
    else:
        import frappe.utils.pdf as fpdf
        return bool(
            getattr(fpdf.pdfkit.from_string, "_cskh_pdf_wrapped", False)
            and fpdf.get_cookie_options() == {}
        )

with ThreadPoolExecutor(max_workers=8) as pool:
    race_ok = list(pool.map(_race_import, range(8)))

from weasyprint import HTML
import pdfkit
wp_blocked = False
try:
    HTML(filename="/etc/passwd")
except ValueError:
    wp_blocked = True

pk_blocked = False
try:
    pdfkit.from_url("http://127.0.0.1:9/")
except PermissionError:
    pk_blocked = True

from crm_cskh.services import pdf_assets
print("LAZY_SUBPROC_JSON:" + json.dumps({
    "import_ms": round((t1 - t0) * 1000.0, 3),
    "call_ms": round((t2 - t1) * 1000.0, 3),
    "eager_after_hook": eager_after_hook,
    "race_ok": race_ok,
    "wp_blocked": wp_blocked,
    "pk_blocked": pk_blocked,
    "wrap_install_count": pdf_assets._WRAP_INSTALL_COUNT,
}))
"""
            proc = subprocess.run(
                [sys.executable, "-c", sub_py],
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(fake_sp),
                            str(REPO_ROOT / "apps" / "crm_cskh"),
                            os.environ.get("PYTHONPATH", ""),
                        ]
                    ),
                },
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"{proc.stderr}\n{proc.stdout}")
            payload = json.loads(
                [
                    ln[len("LAZY_SUBPROC_JSON:") :]
                    for ln in proc.stdout.splitlines()
                    if ln.startswith("LAZY_SUBPROC_JSON:")
                ][-1]
            )
            for mod_name, loaded in payload["eager_after_hook"].items():
                self.assertFalse(loaded, f"Eagerly loaded {mod_name}: {payload}")
            self.assertEqual(payload["race_ok"], [True] * 8)
            self.assertTrue(payload["wp_blocked"])
            self.assertTrue(payload["pk_blocked"])
            self.assertEqual(payload["wrap_install_count"], 1)
            self.assertLess(float(payload["import_ms"]), 250.0, payload)
            self.assertLess(float(payload["call_ms"]), 25.0, payload)

    def test_legacy_dev_frappe_v15_123_1_cssutils_import_ssrf_and_redirect_lockdown(
        self,
    ) -> None:
        """Verify `F-P4-LEGACY-DEV-PDF-CSS-SSRF-01` guard blocks `cssutils.parseString` `@import` SSRF & redirects."""
        import http.server

        hits: list[str] = []

        class _ProbeHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hits.append(self.path)
                if self.path.startswith("/redirect"):
                    self.send_response(302)
                    self.send_header(
                        "Location",
                        f"http://127.0.0.1:{self.server.server_port}/ssrf.png",
                    )
                    self.end_headers()
                    return
                body = b".print-format { margin-top: 99mm; }"
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _fmt: str, *_args: object) -> None:
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        port = srv.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                sites = root / "sites"
                css_dir = sites / "assets" / "crm_cskh" / "css"
                css_dir.mkdir(parents=True)
                (css_dir / "local-print.css").write_text(
                    ".print-format { margin-top: 24mm; }", encoding="utf-8"
                )
                (sites / "dev.localhost" / "public" / "files").mkdir(parents=True)

                fake_sp = root / "fake_sp"
                (fake_sp / "cssutils").mkdir(parents=True)
                (fake_sp / "cssutils" / "_fetch.py").write_text(
                    "import urllib.request\n"
                    "def _defaultFetcher(url):\n"
                    "    with urllib.request.urlopen(url, timeout=5) as r:\n"
                    "        return ('utf-8', r.read().decode('utf-8', 'replace'))\n",
                    encoding="utf-8",
                )
                (fake_sp / "cssutils" / "util.py").write_text(
                    "from ._fetch import _defaultFetcher\n"
                    "def _readUrl(url, fetcher=None):\n"
                    "    fn = fetcher or _defaultFetcher\n"
                    "    return fn(url)\n",
                    encoding="utf-8",
                )
                (fake_sp / "cssutils" / "__init__.py").write_text(
                    "import re, types\n"
                    "from . import _fetch, util\n"
                    "_IMP_RE = re.compile(r'@import\\s+(?:url\\()?[\"' + \"'\" + r']?([^\"' + \"'\" + r');\\s]+)[\"' + \"'\" + r']?\\)?', re.I)\n"
                    "class _StyleRule:\n"
                    "    def __init__(self, selectorText, props):\n"
                    "        self.selectorText = selectorText\n"
                    "        self.style = [types.SimpleNamespace(name=k, value=v) for k, v in props]\n"
                    "css = types.SimpleNamespace(CSSStyleRule=_StyleRule)\n"
                    "class CSSParser:\n"
                    "    def __init__(self, log=None, loglevel=None, raiseExceptions=None, fetcher=None, parseComments=True, validate=True):\n"
                    "        self._fetcher = fetcher\n"
                    "    def setFetcher(self, fetcher=None):\n"
                    "        self._fetcher = fetcher\n"
                    "    def parseString(self, text, **_kw):\n"
                    "        rules = []\n"
                    "        for m in _IMP_RE.finditer(text or ''):\n"
                    "            _, sub_txt = util._readUrl(m.group(1), fetcher=self._fetcher)\n"
                    "            if sub_txt:\n"
                    "                rules.extend(self.parseString(sub_txt))\n"
                    "        if 'margin-top: 24mm' in (text or ''):\n"
                    "            rules.append(_StyleRule('.print-format', [('margin-top', '24mm')]))\n"
                    "        return rules\n"
                    "    def parseUrl(self, href, **_kw):\n"
                    "        _, sub_txt = util._readUrl(href, fetcher=self._fetcher)\n"
                    "        return self.parseString(sub_txt or '')\n"
                    "    def parseFile(self, filename, **_kw):\n"
                    "        return open(filename, 'rb').read()\n"
                    "def parseString(text, **kw):\n"
                    "    return CSSParser().parseString(text, **kw)\n"
                    "def parseUrl(href, **kw):\n"
                    "    return CSSParser().parseUrl(href, **kw)\n"
                    "def parseFile(filename, **kw):\n"
                    "    return CSSParser().parseFile(filename, **kw)\n",
                    encoding="utf-8",
                )
                (fake_sp / "pdfkit").mkdir(parents=True)
                (fake_sp / "pdfkit" / "__init__.py").write_text(
                    "def from_string(html, *a, **kw):\n    return b'%PDF-1.4\\n%%EOF'\n"
                    "def from_url(*a, **kw):\n    return b''\n"
                    "def from_file(*a, **kw):\n    return b''\n",
                    encoding="utf-8",
                )
                (fake_sp / "frappe" / "utils").mkdir(parents=True)
                (fake_sp / "frappe" / "__init__.py").write_text(
                    "import os, types\n"
                    f"local = types.SimpleNamespace(sites_path={str(sites)!r}, site='dev.localhost', request=types.SimpleNamespace(host='dev.localhost'))\n"
                    "session = types.SimpleNamespace(user='Administrator', sid='sid')\n"
                    f"get_site_path = lambda *p: os.path.join({str(sites)!r}, 'dev.localhost', *p)\n"
                    "get_all = lambda *a, **k: []\n"
                    "has_permission = lambda *a, **k: True\n",
                    encoding="utf-8",
                )
                (fake_sp / "frappe" / "utils" / "__init__.py").write_text(
                    "get_url = lambda: 'http://dev.localhost'\n",
                    encoding="utf-8",
                )
                # Exact Frappe v15.123.1 (`d2efa796`) pattern calling `read_options_from_html` + `cssutils.parseString(stylesheet)` BEFORE `pdfkit.from_string`
                (fake_sp / "frappe" / "utils" / "pdf.py").write_text(
                    "import cssutils, pdfkit\n"
                    "from bs4 import BeautifulSoup\n"
                    "_get_base64_image = lambda s: None\n"
                    "inline_private_images = lambda h: h\n"
                    "get_cookie_options = lambda: {'cookie-jar': '/tmp/bad.jar'}\n"
                    "def get_print_format_styles(soup):\n"
                    "    stylesheet = ''.join((t.string or '') for t in soup.find_all('style'))\n"
                    "    parsed = cssutils.parseString(stylesheet)\n"
                    "    out = []\n"
                    "    for r in parsed:\n"
                    "        if isinstance(r, cssutils.css.CSSStyleRule) and '.print-format' in r.selectorText:\n"
                    "            out.extend(r.style)\n"
                    "    return out\n"
                    "def read_options_from_html(html):\n"
                    "    soup = BeautifulSoup(html, 'html.parser')\n"
                    "    styles = get_print_format_styles(soup)\n"
                    "    opts = {s.name: s.value for s in styles}\n"
                    "    return str(soup), opts\n"
                    "def get_pdf(html):\n"
                    "    rewritten, opts = read_options_from_html(html)\n"
                    "    return pdfkit.from_string(rewritten, options=opts), opts, rewritten\n",
                    encoding="utf-8",
                )

                sub_py = r"""
import json, sys, urllib.request
from crm_cskh.services import pdf_hooks

origin = sys.argv[1]
pdf_hooks.ensure_pdf_runtime_hooks()
pre_loaded = {k: (k in sys.modules) for k in ("cssutils", "frappe.utils.pdf", "pdfkit")}

import frappe
from frappe.utils.pdf import get_pdf
import cssutils

html = (
    '<html><head>'
    f'<link rel="stylesheet" href="{origin}/evil.css">'
    '<style>'
    f'@import "{origin}/evil.css"; '
    f'@import url("{origin}/redirect"); '
    '@import "file:///etc/passwd"; '
    '@import "/assets/crm_cskh/css/local-print.css";'
    '</style></head><body><p>DEV v15.123.1</p></body></html>'
)
_, opts, rewritten = get_pdf(html)
cssutils.parseString(f'@import "{origin}/evil.css"; @import url("{origin}/redirect");')

frappe.local.request.host = origin.split("://", 1)[-1]
same_site_css = f"{origin}/assets/crm_cskh/css/local-print.css"
same_site_redir = f"{origin}/redirect"
custom_calls = []
def _network_fetcher(u):
    custom_calls.append(u)
    with urllib.request.urlopen(u, timeout=5) as r:
        return ("utf-8", r.read().decode("utf-8", "replace"))

ctor_rules = cssutils.CSSParser(fetcher=_network_fetcher).parseUrl(same_site_css)
ctor_pos_rules = cssutils.CSSParser(None, None, None, _network_fetcher).parseUrl(same_site_css)
cssutils.CSSParser(fetcher=_network_fetcher).parseUrl(same_site_redir)
setter_p = cssutils.CSSParser()
setter_p.setFetcher(_network_fetcher)
setter_rules = setter_p.parseUrl(same_site_css)
setter_p.parseUrl(same_site_redir)
setter_kw_p = cssutils.CSSParser()
setter_kw_p.setFetcher(fetcher=_network_fetcher)
setter_kw_rules = setter_kw_p.parseUrl(same_site_css)
setter_kw_p.parseUrl(same_site_redir)

parse_file_blocked = False
try:
    cssutils.parseFile("/etc/hosts")
except PermissionError:
    parse_file_blocked = True

print("DEV_CSS_SSRF_JSON:" + json.dumps({
    "pre_loaded": pre_loaded,
    "opts_margin_top": opts.get("margin-top"),
    "evil_in_rewritten": "/evil.css" in rewritten,
    "ctor_rules_len": len(ctor_rules),
    "ctor_pos_rules_len": len(ctor_pos_rules),
    "setter_rules_len": len(setter_rules),
    "setter_kw_rules_len": len(setter_kw_rules),
    "custom_calls": custom_calls,
    "parse_file_blocked": parse_file_blocked,
}))
"""
                proc = subprocess.run(
                    [sys.executable, "-c", sub_py, origin],
                    env={
                        **os.environ,
                        "PYTHONPATH": os.pathsep.join(
                            [
                                str(fake_sp),
                                str(REPO_ROOT / "apps" / "crm_cskh"),
                                os.environ.get("PYTHONPATH", ""),
                            ]
                        ),
                    },
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, f"{proc.stderr}\n{proc.stdout}")
                res = json.loads(
                    [
                        ln[len("DEV_CSS_SSRF_JSON:") :]
                        for ln in proc.stdout.splitlines()
                        if ln.startswith("DEV_CSS_SSRF_JSON:")
                    ][-1]
                )
                self.assertEqual(
                    res["pre_loaded"],
                    {"cssutils": False, "frappe.utils.pdf": False, "pdfkit": False},
                )
                self.assertEqual(res["opts_margin_top"], "24mm")
                self.assertFalse(res["evil_in_rewritten"])
                self.assertEqual(res["ctor_rules_len"], 1)
                self.assertEqual(res["ctor_pos_rules_len"], 1)
                self.assertEqual(res["setter_rules_len"], 1)
                self.assertEqual(res["setter_kw_rules_len"], 1)
                self.assertEqual(res["custom_calls"], [])
                self.assertTrue(res["parse_file_blocked"])
                self.assertEqual(
                    hits,
                    [],
                    f"Legacy DEV v15.123.1 simulation leaked SSRF/redirect hits: {hits}",
                )
        finally:
            srv.shutdown()
            srv.server_close()

    def test_weasyprint_69_fontconfig_double_free_guard_and_v70_noop(self) -> None:
        """Verify `IR-B9-NATIVE-CONCURRENT-02` (`Kozea/WeasyPrint#2843`) fixes WeasyPrint < 70 `FcConfig` double-free and no-ops on >= 70."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            def _make_fake_weasyprint(sp_dir: Path, *, buggy_v69: bool) -> None:
                (sp_dir / "weasyprint" / "text").mkdir(parents=True)
                (sp_dir / "weasyprint" / "urls.py").write_text(
                    "class URLFetcher:\n"
                    "    def __init__(self, fail_on_errors=False, allow_redirects=True, allowed_protocols=None):\n"
                    "        self.allow_redirects = allow_redirects\n"
                    "        self.allowed_protocols = allowed_protocols\n"
                    "class URLFetcherResponse:\n"
                    "    def __init__(self, url, body=None, headers=None, status=200):\n"
                    "        self.url = url\n"
                    "        self.body = body\n",
                    encoding="utf-8",
                )
                config_line = (
                    "        self._config = ffi.gc(fontconfig.FcInitLoadConfigAndFonts(), fontconfig.FcConfigDestroy)\n"
                    if buggy_v69
                    else "        self._config = fontconfig.FcInitLoadConfigAndFonts()\n"
                )
                (sp_dir / "weasyprint" / "text" / "fonts.py").write_text(
                    "import types\n"
                    "STATE = {'refcount': 0, 'destroyed': 0, 'double_free': False, 'gc_callbacks': []}\n"
                    "class _FFI:\n"
                    "    def gc(self, obj, destructor):\n"
                    "        STATE['gc_callbacks'].append((obj, destructor))\n"
                    "        return obj\n"
                    "    def cast(self, _ctype, obj):\n"
                    "        return obj\n"
                    "ffi = _FFI()\n"
                    "def _fc_init():\n"
                    "    STATE['refcount'] += 1\n"
                    "    return {'kind': 'FcConfig'}\n"
                    "def _fc_destroy(_cfg):\n"
                    "    STATE['refcount'] -= 1\n"
                    "    if STATE['refcount'] == 0:\n"
                    "        STATE['destroyed'] += 1\n"
                    "    elif STATE['refcount'] < 0:\n"
                    "        STATE['double_free'] = True\n"
                    "fontconfig = types.SimpleNamespace(FcInitLoadConfigAndFonts=_fc_init, FcConfigDestroy=_fc_destroy)\n"
                    "pangoft2 = types.SimpleNamespace(\n"
                    "    pango_ft2_font_map_new=lambda: {'kind': 'FontMap', 'cfg': None},\n"
                    "    pango_fc_font_map_set_config=lambda fm, cfg: (fm.__setitem__('cfg', cfg), STATE.__setitem__('refcount', STATE['refcount'] + 1)),\n"
                    ")\n"
                    "gobject = types.SimpleNamespace(g_object_unref=lambda fm: _fc_destroy(fm['cfg']))\n"
                    "class FontConfiguration:\n"
                    "    def __init__(self):\n"
                    + config_line
                    + "        self.font_map = ffi.gc(pangoft2.pango_ft2_font_map_new(), gobject.g_object_unref)\n"
                    "        pangoft2.pango_fc_font_map_set_config(ffi.cast('PangoFcFontMap *', self.font_map), self._config)\n"
                    "        fontconfig.FcConfigDestroy(self._config)\n"
                    "        self._folder = None\n"
                    "        self.strut_layouts = {}\n"
                    "        self.font_features = {}\n",
                    encoding="utf-8",
                )
                (sp_dir / "weasyprint" / "__init__.py").write_text(
                    f"__version__ = {'69.0' if buggy_v69 else '70.0'!r}\n"
                    "class HTML:\n"
                    "    def __init__(self, **kwargs):\n"
                    "        self.kwargs = kwargs\n"
                    "class CSS:\n"
                    "    def __init__(self, **kwargs):\n"
                    "        self.kwargs = kwargs\n",
                    encoding="utf-8",
                )
                (sp_dir / "frappe" / "utils").mkdir(parents=True)
                (sp_dir / "frappe" / "__init__.py").write_text(
                    "import types\n"
                    "local = types.SimpleNamespace(sites_path='.', site='fc.localhost', request=None)\n"
                    "session = types.SimpleNamespace(user='Administrator', sid='sid')\n"
                    "get_site_path = lambda *p: '/'.join(('sites', 'fc.localhost', *p))\n"
                    "get_all = lambda *a, **k: []\n"
                    "has_permission = lambda *a, **k: True\n",
                    encoding="utf-8",
                )
                (sp_dir / "frappe" / "utils" / "__init__.py").write_text(
                    "get_url = lambda: 'http://fc.localhost'\n",
                    encoding="utf-8",
                )

            probe_py = r"""
import json, sys
use_hooks = sys.argv[1] == "1"
if use_hooks:
    from crm_cskh.services import pdf_hooks
    pdf_hooks.ensure_pdf_runtime_hooks()
    eager_fonts = "weasyprint.text.fonts" in sys.modules
else:
    eager_fonts = False

import weasyprint
from weasyprint.text import fonts as wp_fonts

fc = wp_fonts.FontConfiguration()
attrs_ok = (fc._folder is None and fc.strut_layouts == {} and fc.font_features == {})
for obj, dtor in list(wp_fonts.STATE["gc_callbacks"]):
    dtor(obj)

print("FC_GUARD_JSON:" + json.dumps({
    "eager_fonts": eager_fonts,
    "patched": bool(getattr(wp_fonts.FontConfiguration.__init__, "_cskh_fc_double_free_fixed", False)),
    "attrs_ok": attrs_ok,
    "refcount": wp_fonts.STATE["refcount"],
    "destroyed": wp_fonts.STATE["destroyed"],
    "double_free": wp_fonts.STATE["double_free"],
}))
"""

            def _run_case(sp_dir: Path, *, use_hooks: bool) -> dict:
                proc = subprocess.run(
                    [sys.executable, "-c", probe_py, "1" if use_hooks else "0"],
                    env={
                        **os.environ,
                        "PYTHONPATH": os.pathsep.join(
                            [
                                str(sp_dir),
                                str(REPO_ROOT / "apps" / "crm_cskh"),
                                os.environ.get("PYTHONPATH", ""),
                            ]
                        ),
                    },
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, f"{proc.stderr}\n{proc.stdout}")
                return json.loads(
                    [
                        ln[len("FC_GUARD_JSON:") :]
                        for ln in proc.stdout.splitlines()
                        if ln.startswith("FC_GUARD_JSON:")
                    ][-1]
                )

            sp_v69 = root / "sp_v69"
            sp_v70 = root / "sp_v70"
            _make_fake_weasyprint(sp_v69, buggy_v69=True)
            _make_fake_weasyprint(sp_v70, buggy_v69=False)

            unpatched_v69 = _run_case(sp_v69, use_hooks=False)
            self.assertFalse(unpatched_v69["patched"])
            self.assertTrue(unpatched_v69["double_free"])
            self.assertEqual(unpatched_v69["refcount"], -1)

            guarded_v69 = _run_case(sp_v69, use_hooks=True)
            self.assertFalse(guarded_v69["eager_fonts"])
            self.assertTrue(guarded_v69["patched"])
            self.assertTrue(guarded_v69["attrs_ok"])
            self.assertFalse(guarded_v69["double_free"])
            self.assertEqual(guarded_v69["refcount"], 0)
            self.assertEqual(guarded_v69["destroyed"], 1)

            guarded_v70 = _run_case(sp_v70, use_hooks=True)
            self.assertFalse(guarded_v70["eager_fonts"])
            self.assertFalse(guarded_v70["patched"])
            self.assertTrue(guarded_v70["attrs_ok"])
            self.assertFalse(guarded_v70["double_free"])
            self.assertEqual(guarded_v70["refcount"], 0)
            self.assertEqual(guarded_v70["destroyed"], 1)


if __name__ == "__main__":
    unittest.main()
