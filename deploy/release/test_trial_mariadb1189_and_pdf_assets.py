#!/usr/bin/env python3
"""Unit & contract tests for MariaDB 1:11.8.9+ds-0+deb13u1 + INSTALL_CHROMIUM=false + PDF local assets."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_pdf_assets_with_stub_frappe(sites_dir: Path, site_name: str):
    """Loads `crm_cskh.services.pdf_assets` against a lightweight stub `frappe` module."""
    stub_frappe = types.ModuleType("frappe")
    stub_local = types.SimpleNamespace(
        sites_path=str(sites_dir),
        site=site_name,
        request=types.SimpleNamespace(host=site_name),
    )
    stub_frappe.local = stub_local  # type: ignore[attr-defined]
    stub_utils = types.ModuleType("frappe.utils")
    stub_utils.get_url = lambda: f"http://{site_name}"  # type: ignore[attr-defined]
    stub_frappe.utils = stub_utils  # type: ignore[attr-defined]
    stub_frappe.get_site_path = lambda *parts: str(sites_dir / site_name / Path(*parts))  # type: ignore[attr-defined]

    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
    sys.modules["frappe"] = stub_frappe
    sys.modules["frappe.utils"] = stub_utils
    try:
        mod_path = (
            REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "services" / "pdf_assets.py"
        )
        spec = importlib.util.spec_from_file_location("_ut_pdf_assets", mod_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, saved
    except Exception:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        raise


class TestTrialMariaDb1189AndPdfAssets(unittest.TestCase):
    def test_release_lock_and_containerfile_mariadb1189_and_no_chromium_pins(
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
        sha_map = lock["security_patches"]["mariadb_client_amd64_sha256"]
        self.assertEqual(
            set(sha_map),
            {
                "mariadb-common",
                "libmariadb3",
                "mariadb-client-core",
                "mariadb-client",
                "libmariadb-dev",
            },
        )
        for pkg, sha_hex in sha_map.items():
            self.assertEqual(len(sha_hex), 64, pkg)
            self.assertIn(sha_hex, cfile)

        self.assertIn("ARG INSTALL_CHROMIUM=false", cfile)
        self.assertIn("ARG MARIADB_DEB_VERSION=1:11.8.9+ds-0+deb13u1", cfile)
        self.assertIn(
            "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg", cfile
        )
        self.assertEqual(exc_doc.get("exceptions"), [])

    def test_pdf_assets_inlines_same_site_css_and_images_and_blocks_traversal(
        self,
    ) -> None:
        try:
            import bs4  # noqa: F401
        except ImportError:
            self.skipTest("bs4 not installed in host Python")

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
                '@font-face { src: url("font.woff2"); } body { color: #111; }',
                encoding="utf-8",
            )
            png_bytes = b"\x89PNG\r\n\x1a\nFAKEPNG"
            (public_files / "badge.png").write_bytes(png_bytes)
            (root / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

            mod, saved = _load_pdf_assets_with_stub_frappe(sites, "p4-smoke.localhost")
            try:
                raw_html = (
                    "<html><head>"
                    '<link type="text/css" rel="stylesheet" '
                    'href="http://p4-smoke.localhost/assets/frappe/dist/css/print.bundle.123.css">'
                    "</head><body>"
                    '<img src="http://p4-smoke.localhost/files/badge.png"/>'
                    '<img src="http://p4-smoke.localhost/files/../../../secret.txt"/>'
                    '<img src="https://evil.example.invalid/ssrf.png"/>'
                    "</body></html>"
                )
                out = mod.inline_local_pdf_assets(raw_html)
                self.assertNotIn("print.bundle.123.css", out)
                self.assertIn(base64.b64encode(font_bytes).decode("ascii"), out)
                self.assertIn(
                    f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}",
                    out,
                )
                self.assertNotIn("TOP_SECRET", out)
                self.assertNotIn(base64.b64encode(b"TOP_SECRET").decode("ascii"), out)
                self.assertIn("https://evil.example.invalid/ssrf.png", out)

                # Verify depth-first _cskh_get_element finds <header> even after a MarginBox sibling
                class _Box:
                    def __init__(self, tag, children=()):
                        self.element_tag = tag
                        self._children = list(children)

                    def all_children(self):
                        return self._children

                tree = [
                    _Box(None, [_Box("span")]),  # @page MarginBox preceding <html>
                    _Box("html", [_Box("body", [_Box("div"), _Box("header")])]),
                ]
                found = mod._cskh_get_element(tree, "header")
                self.assertIsNotNone(found)
                self.assertEqual(found.element_tag, "header")
            finally:
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v


if __name__ == "__main__":
    unittest.main()
