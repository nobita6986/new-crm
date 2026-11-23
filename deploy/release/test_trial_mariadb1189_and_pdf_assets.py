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


if __name__ == "__main__":
    unittest.main()
