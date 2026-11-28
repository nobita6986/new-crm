"""Offline branding-cache regressions with seeded stale docs, not a CI-race reproduction."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
import types
import unittest
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = REPO_ROOT / "apps/crm_cskh/crm_cskh/services/runtime_cache.py"
SMOKE_PATH = REPO_ROOT / "deploy/compose/scripts/run_isolated_smoke.py"
DEFAULT = {"favicon": "/default.svg", "splash_image": ""}
BRAND = {"favicon": "/logo.svg?fav=v1", "splash_image": "/logo.svg?brand=v1"}


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Callbacks:
    """Frappe CallbackManager drains callbacks added while the queue runs."""

    def __init__(self):
        self.items = deque()

    def add(self, callback):
        self.items.append(callback)

    def run(self):
        count = 0
        while self.items:
            self.items.popleft()()
            count += 1
            if count > 100:
                raise AssertionError("Recursive transaction cache invalidation")


class _BrandingState:
    def __init__(self):
        self.committed = deepcopy(DEFAULT)
        self.docs = {"Website Settings": deepcopy(DEFAULT)}
        self.events = []
        self.sql_reads = 0
        self.local = types.SimpleNamespace(
            site="branding.localhost", request=types.SimpleNamespace(path="/crm/leads")
        )
        self.after_commit = _Callbacks()
        self.after_rollback = _Callbacks()

    def sql(self, query, **kwargs):
        self.sql_reads += 1
        return [
            ("Website Settings", key, value) for key, value in self.committed.items()
        ]

    def get_cached_doc(self, doctype):
        if doctype not in self.docs:
            self.docs[doctype] = (
                deepcopy(self.committed) if doctype == "Website Settings" else {}
            )
        return self.docs[doctype]

    def clear_document_cache(self, doctype, name):
        # Match the pinned Frappe immediate + post-transaction deletion behavior.
        def clear():
            self.docs.pop(doctype, None)

        self.events.append(("purge", doctype))
        clear()
        self.after_commit.add(clear)
        self.after_rollback.add(clear)
        if doctype == "Website Settings" and hasattr(self.local, "website_settings"):
            del self.local.website_settings


@contextmanager
def _runtime_harness():
    state = _BrandingState()
    frappe = types.ModuleType("frappe")
    frappe.local = state.local
    frappe.db = state
    frappe.flags = types.SimpleNamespace(
        in_test=False, in_migrate=False, in_install=False
    )
    frappe.get_hooks = lambda *_: {}
    frappe.get_cached_doc = state.get_cached_doc

    def recursive_wrapper(*args):
        raise AssertionError(
            "Purge must bypass the epoch-bumping document-cache wrapper"
        )

    recursive_wrapper._cskh_orig = state.clear_document_cache
    frappe.clear_document_cache = recursive_wrapper
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda value=0: int(value or 0)
    renderer = types.ModuleType("crm_cskh.www.cskh_renderer")
    renderer.install_template_page_fastpath = lambda: None
    modules = {"frappe": frappe, "frappe.utils": utils, renderer.__name__: renderer}
    for name in ("crm_cskh", "crm_cskh.www"):
        modules[name] = types.ModuleType(name)
        modules[name].__path__ = []
    with mock.patch.dict(sys.modules, modules):
        runtime = _load_module("branding_runtime_under_test", RUNTIME_PATH)
        runtime._redis_mod = (
            None  # Use the real deterministic in-process epoch fallback.
        )
        runtime._ensure_mutation_hooks_installed = mock.Mock()
        runtime.install_inprocess_translation_cache = mock.Mock()
        yield runtime, frappe, state


class BrandingCacheTests(unittest.TestCase):
    def test_transaction_boundary_purges_repopulated_branding_and_bumps_once(self):
        for boundary in ("after_commit", "after_rollback"):
            with (
                self.subTest(boundary=boundary),
                _runtime_harness() as (runtime, _, state),
            ):
                state.committed = deepcopy(
                    BRAND if boundary == "after_commit" else DEFAULT
                )
                runtime.bump_cache_domain(runtime.DOMAIN_BRANDING)
                runtime.bump_cache_domain(runtime.DOMAIN_BRANDING)
                # A document was repopulated after the earlier invalidations.
                state.docs["Website Settings"] = {"favicon": "/uncommitted-or-old.svg"}
                state.local.website_settings = state.docs["Website Settings"]
                before_epoch = runtime.get_domain_epoch(runtime.DOMAIN_BRANDING)
                original_bump = runtime._bump_domains_once

                def observe_bump(domains):
                    self.assertNotIn("Website Settings", state.docs)
                    self.assertFalse(hasattr(state.local, "website_settings"))
                    state.events.append(("epoch", tuple(domains)))
                    original_bump(domains)

                with mock.patch.object(
                    runtime, "_bump_domains_once", side_effect=observe_bump
                ):
                    getattr(state, boundary).run()
                self.assertEqual(
                    state.get_cached_doc("Website Settings"), state.committed
                )
                self.assertEqual(
                    runtime.get_domain_epoch(runtime.DOMAIN_BRANDING), before_epoch + 1
                )
                self.assertEqual(sum(event[0] == "epoch" for event in state.events), 1)
                self.assertEqual(
                    sum(
                        event == ("purge", "Website Settings") for event in state.events
                    ),
                    1,
                )

    def test_unrelated_transaction_does_not_purge_branding(self):
        with _runtime_harness() as (runtime, _, state):
            cached = state.docs["Website Settings"]
            runtime.bump_cache_domain(runtime.DOMAIN_WORK_ITEM)
            state.after_commit.run()
            self.assertIs(state.docs["Website Settings"], cached)
            self.assertEqual(state.events, [])

    def test_crm_epoch_change_reconciles_sql_with_stale_document_before_page_read(self):
        for route in ("/crm", "/crm/leads"):
            with self.subTest(route=route), _runtime_harness() as (runtime, _, state):
                state.local.request.path = route
                runtime.get_website_branding()  # Warm the old epoch's LRU and document.
                state.committed = deepcopy(BRAND)
                runtime.bump_cache_domain(runtime.DOMAIN_BRANDING)
                state.local.website_settings = state.docs["Website Settings"]
                runtime.on_before_request()
                self.assertFalse(hasattr(state.local, "website_settings"))
                self.assertEqual(state.get_cached_doc("Website Settings"), BRAND)
                self.assertEqual(
                    runtime.get_website_branding()["favicon"], BRAND["favicon"]
                )
                reads = state.sql_reads
                runtime.on_before_request()
                self.assertEqual(state.sql_reads, reads)

                # A Redis generation change with a repeated counter is a distinct epoch.
                runtime._SITE_GENS[state.local.site] += 1
                state.committed = {
                    "favicon": "/logo.svg?fav=v2",
                    "splash_image": "/logo.svg?brand=v2",
                }
                runtime.on_before_request()
                self.assertEqual(
                    state.get_cached_doc("Website Settings"), state.committed
                )
                self.assertEqual(state.sql_reads, reads + 1)
                runtime.clear_all_local_caches()
                runtime.on_before_request()
                self.assertEqual(state.sql_reads, reads + 2)

    def test_non_crm_and_disabled_runtime_requests_do_not_read_branding(self):
        for route, in_test in (
            ("/cskh/dashboard", False),
            ("/crm-extra", False),
            ("/crm", True),
        ):
            with (
                self.subTest(route=route, in_test=in_test),
                _runtime_harness() as (runtime, frappe, state),
            ):
                state.local.request.path = route
                frappe.flags.in_test = in_test
                runtime.on_before_request()
                self.assertEqual(state.sql_reads, 0)


class BrandingSmokeDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smoke = _load_module("branding_smoke_under_test", SMOKE_PATH)

    def check(self, status, body, **kwargs):
        logger = self.smoke.SmokeLogger()
        with self.assertRaises(AssertionError) as caught:
            self.smoke.check_crm_branding_reload(
                status,
                body,
                phase="v1",
                reload_index=3,
                favicon=BRAND["favicon"],
                logo=BRAND["splash_image"],
                logger=logger,
                require_prepaint=True,
                **kwargs,
            )
        detail = json.loads(str(caught.exception).split(": ", 1)[1])
        self.assertIn("before branding reset", logger.render())
        self.assertEqual(detail["reload_index"], 3)
        self.assertEqual(detail["http_status"], status)
        return detail

    def test_http_failure_is_distinct_from_a_mismatched_favicon(self):
        detail = self.check(503, '<link rel="icon" href="/default.svg">')
        self.assertIn("http_status", detail["failures"])
        self.assertIn("HTTP 503", detail["error"])
        self.assertEqual(detail["actual_icon_hrefs"], ["/default.svg"])
        self.assertFalse(detail["prepaint_marker_present"])
        detail = self.check(200, '<link rel="shortcut icon" href="/default.svg">')
        self.assertNotIn("http_status", detail["failures"])
        self.assertIn("favicon", detail["failures"])
        self.assertIn("Branding mismatch on HTTP 200", detail["error"])

    def test_reload_diagnostics_scrub_registered_and_cookie_secrets_in_hrefs_and_errors(
        self,
    ):
        logger = self.smoke.SmokeLogger()
        secret_tok = "AdmSecretToken_987654321"
        raw_sid = "0123456789abcdef0123456789abcdef"
        logger.register_secret(secret_tok, "<REDACTED_ADMIN_PASSWORD>")
        with self.assertRaises(AssertionError) as caught:
            self.smoke.check_crm_branding_reload(
                200,
                f'<link rel="icon" href="/bad.svg?sid={raw_sid}&k={secret_tok}">',
                phase="v2",
                reload_index=5,
                favicon=BRAND["favicon"],
                logo=BRAND["splash_image"],
                logger=logger,
            )
        exc_text = str(caught.exception)
        rendered_log = logger.render()
        for leak in (secret_tok, raw_sid, "sid=", "k="):
            self.assertNotIn(leak, exc_text)
            self.assertNotIn(leak, rendered_log)
        detail = json.loads(exc_text.split(": ", 1)[1])
        self.assertEqual(detail["reload_index"], 5)
        self.assertEqual(detail["http_status"], 200)
        self.assertEqual(len(detail["actual_icon_hrefs"]), 1)
        self.assertRegex(
            detail["actual_icon_hrefs"][0],
            r"^/bad\.svg\?<REDACTED_QUERY:sha256=[0-9a-f]{12}>$",
        )
        v1_safe = logger.sanitize_url("/assets/crm_cskh/cskh/logo.svg?fav=smoke-v1")
        v2_safe = logger.sanitize_url("/assets/crm_cskh/cskh/logo.svg?fav=smoke-v2")
        self.assertTrue(
            v1_safe.startswith("/assets/crm_cskh/cskh/logo.svg?<REDACTED_QUERY:sha256=")
        )
        self.assertNotEqual(v1_safe, v2_safe)

    def test_signed_urls_userinfo_fragments_and_data_uris_are_redacted_across_fields(
        self,
    ):
        logger = self.smoke.SmokeLogger()
        signed_expected = (
            "https://exp-user:exp-pass@cdn.example.invalid/brand/expected.svg"
            "?X-Amz-Credential=SYNTHETIC_EXP_CRED"
            "&X-Amz-Signature=SYNTHETIC_EXP_SIG#exp_frag=SYNTHETIC_EXP_FRAG"
        )
        signed_actual = (
            "https://dummy-user:dummy-pass@assets.example.invalid:8443/icon.svg"
            "?X-Amz-Credential=SYNTHETIC_CREDENTIAL"
            "&X-Amz-Signature=SYNTHETIC_SIGNATURE"
            "&access_token=SYNTHETIC_TOKEN#frag_tok=SYNTHETIC_FRAGMENT"
        )
        proto_rel_actual = (
            "//proto-user:proto-pass@static.example.invalid"
            "/t/0123456789abcdef0123456789abcdef/icon.svg;jsessionid=SYNTHETIC_JSESS"
            "?sig=SYNTHETIC_PROTO_SIG"
        )
        data_svg = 'data:image/svg+xml,<svg width="16">SYNTHETIC_PRIVATE_CONTENT</svg>'
        data_png = "data:image/png;base64,SYNTHETIC_BASE64_PAYLOAD"
        schemeless_cred = "bare-user:bare-pass@assets.example.invalid/icon.svg"
        js_uri = "javascript:alert('SYNTHETIC_JS_PAYLOAD')"
        body = (
            f'<link rel="icon" href="{signed_actual}">'
            f'<link rel="icon" href="{proto_rel_actual}">'
            f"<link rel='icon' href='{data_svg}'>"
            f'<link rel="icon" href="{data_png}">'
            f'<link rel="icon" href="{schemeless_cred}">'
            f'<link rel="icon" href="{js_uri}">'
        )
        with self.assertRaises(AssertionError) as caught:
            self.smoke.check_crm_branding_reload(
                200,
                body,
                phase="v2",
                reload_index=4,
                favicon=signed_expected,
                logo="/expected-logo.svg",
                logger=logger,
            )
        exc_text = str(caught.exception)
        rendered_log = logger.render()
        forbidden = (
            "exp-user",
            "exp-pass",
            "SYNTHETIC_EXP_CRED",
            "SYNTHETIC_EXP_SIG",
            "SYNTHETIC_EXP_FRAG",
            "dummy-user",
            "dummy-pass",
            "SYNTHETIC_CREDENTIAL",
            "SYNTHETIC_SIGNATURE",
            "SYNTHETIC_TOKEN",
            "SYNTHETIC_FRAGMENT",
            "proto-user",
            "proto-pass",
            "0123456789abcdef0123456789abcdef",
            "SYNTHETIC_JSESS",
            "SYNTHETIC_PROTO_SIG",
            "SYNTHETIC_PRIVATE_CONTENT",
            "SYNTHETIC_BASE64_PAYLOAD",
            "bare-user",
            "bare-pass",
            "SYNTHETIC_JS_PAYLOAD",
            "X-Amz-Credential",
            "X-Amz-Signature",
            "access_token",
        )
        for secret_piece in forbidden:
            self.assertNotIn(secret_piece, exc_text)
            self.assertNotIn(secret_piece, rendered_log)

        detail = json.loads(exc_text.split(": ", 1)[1])
        self.assertRegex(
            detail["expected_icon_href"],
            r"^https://cdn\.example\.invalid/brand/expected\.svg"
            r"\?<REDACTED_QUERY:sha256=[0-9a-f]{12}>"
            r"#<REDACTED_FRAGMENT:sha256=[0-9a-f]{12}>$",
        )
        self.assertRegex(
            detail["actual_icon_hrefs"][0],
            r"^https://assets\.example\.invalid:8443/icon\.svg"
            r"\?<REDACTED_QUERY:sha256=[0-9a-f]{12}>"
            r"#<REDACTED_FRAGMENT:sha256=[0-9a-f]{12}>$",
        )
        self.assertRegex(
            detail["actual_icon_hrefs"][1],
            r"^//static\.example\.invalid/t/<REDACTED_SEGMENT>/icon\.svg;<REDACTED_PARAM>"
            r"\?<REDACTED_QUERY:sha256=[0-9a-f]{12}>$",
        )
        self.assertRegex(
            detail["actual_icon_hrefs"][2],
            r"^data:image/svg\+xml;<REDACTED_DATA:sha256=[0-9a-f]{12}>$",
        )
        self.assertRegex(
            detail["actual_icon_hrefs"][3],
            r"^data:image/png;<REDACTED_DATA:sha256=[0-9a-f]{12}>$",
        )
        self.assertEqual(detail["actual_icon_hrefs"][4], "<UNSUPPORTED_SCHEME_URL>")
        self.assertRegex(
            detail["actual_icon_hrefs"][5],
            r"^javascript:<REDACTED_PAYLOAD:sha256=[0-9a-f]{12}>$",
        )
        for item in [detail["expected_icon_href"], *detail["actual_icon_hrefs"]]:
            self.assertLessEqual(len(item), 256)

        err_logger = self.smoke.SmokeLogger()
        transport_msg = (
            f"URLError: upstream {signed_actual} Bearer SYNTHETIC_BEARER "
            'body={"favicon": "data:image/svg+xml,<svg width=\\"16\\">'
            'SYNTHETIC_ERR_SVG</svg>"} X-Amz-Signature=SYNTHETIC_RAW_SIG'
        )
        with self.assertRaises(AssertionError) as err_caught:
            self.smoke.check_crm_branding_reload(
                None,
                "",
                phase="v1",
                reload_index=2,
                favicon=data_svg,
                logo="/expected-logo.svg",
                logger=err_logger,
                transport_error=transport_msg,
            )
        err_exc = str(err_caught.exception)
        err_log = err_logger.render()
        for leak in (
            "dummy-user",
            "dummy-pass",
            "SYNTHETIC_CREDENTIAL",
            "SYNTHETIC_SIGNATURE",
            "SYNTHETIC_TOKEN",
            "SYNTHETIC_BEARER",
            "SYNTHETIC_ERR_SVG",
            "SYNTHETIC_RAW_SIG",
            "SYNTHETIC_PRIVATE_CONTENT",
        ):
            self.assertNotIn(leak, err_exc)
            self.assertNotIn(leak, err_log)
        err_detail = json.loads(err_exc.split(": ", 1)[1])
        self.assertEqual(err_detail["failures"], ["http_transport_error"])
        self.assertRegex(
            err_detail["expected_icon_href"],
            r"^data:image/svg\+xml;<REDACTED_DATA:sha256=[0-9a-f]{12}>$",
        )
        self.assertLessEqual(len(err_detail["error"]), 320)

    def test_malformed_html_on_http_503_preserves_status_and_primary_error(self):
        logger = self.smoke.SmokeLogger()
        malformed_503 = (
            "<![bogus]>"
            '<link rel="icon" href="https://u503:p503@assets.example.invalid/default.svg'
            '?X-Amz-Signature=SYNTHETIC_503_SIG">'
        )
        with self.assertRaises(AssertionError) as caught:
            self.smoke.check_crm_branding_reload(
                503,
                malformed_503,
                phase="v1",
                reload_index=2,
                favicon=BRAND["favicon"],
                logo=BRAND["splash_image"],
                logger=logger,
                require_prepaint=True,
            )
        exc_text = str(caught.exception)
        rendered_log = logger.render()
        self.assertTrue(exc_text.startswith("CRM branding reload failed: "), exc_text)
        self.assertIn("before branding reset", rendered_log)
        for leak in ("u503", "p503", "SYNTHETIC_503_SIG", "X-Amz-Signature"):
            self.assertNotIn(leak, exc_text)
            self.assertNotIn(leak, rendered_log)
        detail = json.loads(exc_text.split(": ", 1)[1])
        self.assertEqual(detail["phase"], "v1")
        self.assertEqual(detail["reload_index"], 2)
        self.assertEqual(detail["http_status"], 503)
        self.assertIn("http_status", detail["failures"])
        self.assertIn("HTTP 503", detail["error"])
        self.assertEqual(detail["icon_parse_error"], "AssertionError")
        self.assertEqual(len(detail["actual_icon_hrefs"]), 1)
        self.assertRegex(
            detail["actual_icon_hrefs"][0],
            r"^https://assets\.example\.invalid/default\.svg"
            r"\?<REDACTED_QUERY:sha256=[0-9a-f]{12}>$",
        )

        mid_detail = self.check(
            503,
            '<link rel="icon" href="/first.svg"><![bogus]><link rel="icon" href="/second.svg">',
        )
        self.assertEqual(mid_detail["http_status"], 503)
        self.assertEqual(mid_detail["actual_icon_hrefs"], ["/first.svg", "/second.svg"])
        self.assertEqual(mid_detail["icon_parse_error"], "AssertionError")

        eof_detail = self.check(
            503, '<link rel="icon" href="/eof.svg"><![CDATA[unterminated'
        )
        self.assertEqual(eof_detail["http_status"], 503)
        self.assertEqual(eof_detail["actual_icon_hrefs"], ["/eof.svg"])

        many_icons = "<![bogus]>" + "".join(
            f'<link rel="icon" href="/ico-{idx}.svg">' for idx in range(12)
        )
        many_detail = self.check(503, many_icons)
        self.assertEqual(many_detail["http_status"], 503)
        self.assertEqual(len(many_detail["actual_icon_hrefs"]), 10)
        self.assertTrue(many_detail["actual_icon_hrefs_truncated"])
        self.assertEqual(many_detail["icon_parse_error"], "AssertionError")

    def test_ast_isolated_smoke_functions_satisfy_independent_probe_harness(self):
        source = SMOKE_PATH.read_text(encoding="utf-8")
        root = ast.parse(source)
        selected = [
            node
            for node in root.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in ("SmokeLogger", "check_crm_branding_reload")
        ]
        module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                *selected,
            ],
            type_ignores=[],
        )
        ns = {
            "dt": dt,
            "hashlib": hashlib,
            "json": json,
            "re": re,
            "HTMLParser": HTMLParser,
        }
        exec(compile(ast.fix_missing_locations(module), "<ast_probe>", "exec"), ns)

        probes = [
            (
                "signed_url",
                200,
                '<link rel="icon" href="https://dummy-user:dummy-pass@assets.example.invalid'
                "/icon.svg?X-Amz-Credential=SYNTHETIC_CREDENTIAL"
                '&X-Amz-Signature=SYNTHETIC_SIGNATURE&access_token=SYNTHETIC_TOKEN">',
            ),
            (
                "data_uri_content",
                200,
                '<link rel="icon" href="data:image/svg+xml,&lt;svg&gt;'
                'SYNTHETIC_PRIVATE_CONTENT&lt;/svg&gt;">',
            ),
            (
                "malformed_declaration",
                503,
                '<![bogus]><link rel="icon" href="/default.svg">',
            ),
        ]
        for name, st, html_body in probes:
            with self.subTest(probe=name):
                probe_logger = ns["SmokeLogger"]()
                with self.assertRaises(AssertionError) as caught:
                    ns["check_crm_branding_reload"](
                        st,
                        html_body,
                        phase="v1",
                        reload_index=1,
                        favicon="/expected.svg",
                        logo="/expected-logo.svg",
                        logger=probe_logger,
                    )
                exc_str = str(caught.exception)
                log_str = probe_logger.render()
                self.assertTrue(
                    exc_str.startswith("CRM branding reload failed: "), exc_str
                )
                self.assertIn("before branding reset", log_str)
                for leak in (
                    "dummy-user",
                    "dummy-pass",
                    "SYNTHETIC_CREDENTIAL",
                    "SYNTHETIC_SIGNATURE",
                    "SYNTHETIC_TOKEN",
                    "SYNTHETIC_PRIVATE_CONTENT",
                ):
                    self.assertNotIn(leak, exc_str)
                    self.assertNotIn(leak, log_str)
                parsed = json.loads(exc_str.split(": ", 1)[1])
                self.assertEqual(parsed["http_status"], st)
                if name == "malformed_declaration":
                    self.assertEqual(parsed["http_status"], 503)
                    self.assertEqual(parsed["actual_icon_hrefs"], ["/default.svg"])
                    self.assertEqual(parsed["icon_parse_error"], "AssertionError")

    def test_prepaint_and_previous_logo_checks_remain_strict(self):
        body = f'<link rel="icon" href="{BRAND["favicon"]}">{BRAND["splash_image"]}'
        self.assertIn("prepaint_logo", self.check(200, body)["failures"])
        body += ' id="hrpartner-branding-prepaint" /old-logo.svg'
        self.assertIn(
            "previous_logo",
            self.check(200, body, previous_logo="/old-logo.svg")["failures"],
        )
        self.smoke.check_crm_branding_reload(
            200,
            body,
            phase="v1",
            reload_index=1,
            favicon=BRAND["favicon"],
            logo=BRAND["splash_image"],
            logger=self.smoke.SmokeLogger(),
            require_prepaint=True,
        )

    def test_smoke_captures_failure_before_the_finally_branding_reset(self):
        logger = self.smoke.SmokeLogger()
        guest, authenticated = object(), object()
        sid = "synthetic-session-for-branding-test"
        csrf = "abcdef0123456789abcdef0123456789"
        jar = [types.SimpleNamespace(name="sid", value=sid)]
        shell = (
            '<head>/assets/crm_cskh/cskh/cskh.css</head> class="cskh-sidebar" '
            f'Asia/Ho_Chi_Minh Tổng quan Chất lượng "X-Frappe-CSRF-Token": "{csrf}"'
        )
        resets = []

        def http(opener, url, site, method="GET", data=None, headers=None):
            path = self.smoke.urllib.parse.urlsplit(url).path
            if opener is guest:
                return 403, {}, ""
            if path == "/api/method/frappe.auth.get_logged_user":
                return 200, {}, "Administrator"
            if path.startswith("/cskh/"):
                return 200, {}, shell
            if path == "/crm":
                return 200, {}, 'data-cskh-crm-theme="1" crm-sidebar-bridge.js'
            if path == "/api/method/crm_cskh.api.p3_reporting.request_recompute":
                return 400, {}, ""
            if path == "/api/method/crm_cskh.api.health.ping":
                if (headers or {}).get("X-Frappe-CSRF-Token") != csrf:
                    return 400, {}, ""
                return 200, {}, '{"message":{"app":"crm_cskh"}}'
            if path == "/api/method/frappe.client.set_value":
                if json.loads(data)["fieldname"]["favicon"] == "":
                    resets.append(logger.render())
                return 200, {}, "{}"
            if path == "/crm/leads":
                return (
                    503,
                    {},
                    '<![bogus]><link rel="icon" href="/old.svg">'
                    '<link rel="icon" href="data:image/svg+xml,<svg>SYNTHETIC_503_SVG</svg>">',
                )
            self.fail(f"Unexpected smoke request: {path}")

        with (
            mock.patch.object(self.smoke, "wait_for_ingress_ping"),
            mock.patch.object(
                self.smoke,
                "make_http_client",
                side_effect=[([], guest), (jar, authenticated)],
            ),
            mock.patch.object(
                self.smoke,
                "http_call_with_cookies",
                return_value=(
                    200,
                    {},
                    [f"sid={sid}; HttpOnly; Path=/; SameSite=Lax"],
                    "{}",
                ),
            ),
            mock.patch.object(self.smoke, "http_call", side_effect=http),
            self.assertRaisesRegex(AssertionError, '"http_status": 503'),
        ):
            self.smoke.run_http_and_socketio_smoke(
                "unused-project",
                Path("unused.env"),
                18080,
                "synthetic.localhost",
                "unused",
                logger,
            )
        self.assertEqual(len(resets), 1)
        self.assertIn('"reload_index": 1', resets[0])
        self.assertIn('"/old.svg"', resets[0])
        self.assertIn('"icon_parse_error": "AssertionError"', resets[0])
        self.assertNotIn("SYNTHETIC_503_SVG", resets[0])
        self.assertIn('"prepaint_marker_present": false', resets[0])
        self.assertIn("before branding reset", resets[0])

    def test_set_branding_failure_sanitizes_fallback_and_reset_diagnostics(self):
        logger = self.smoke.SmokeLogger()
        guest, authenticated = object(), object()
        sid = "synthetic-session-for-fallback-sanitize"
        csrf = "11223344556677889900aabbccddeeff"
        jar = [types.SimpleNamespace(name="sid", value=sid)]
        shell = (
            '<head>/assets/crm_cskh/cskh/cskh.css</head> class="cskh-sidebar" '
            f'Asia/Ho_Chi_Minh Tổng quan Chất lượng "X-Frappe-CSRF-Token": "{csrf}"'
        )
        set_calls = []

        def http(opener, url, site, method="GET", data=None, headers=None):
            path = self.smoke.urllib.parse.urlsplit(url).path
            if opener is guest:
                return 403, {}, ""
            if path == "/api/method/frappe.auth.get_logged_user":
                return 200, {}, "Administrator"
            if path.startswith("/cskh/"):
                return 200, {}, shell
            if path == "/crm":
                return 200, {}, 'data-cskh-crm-theme="1" crm-sidebar-bridge.js'
            if path == "/api/method/crm_cskh.api.p3_reporting.request_recompute":
                return 400, {}, ""
            if path == "/api/method/crm_cskh.api.health.ping":
                return (
                    (200, {}, '{"message":{"app":"crm_cskh"}}')
                    if (headers or {}).get("X-Frappe-CSRF-Token") == csrf
                    else (400, {}, "")
                )
            if path == "/api/method/frappe.client.set_value":
                set_calls.append(json.loads(data)["fieldname"]["favicon"])
                if len(set_calls) == 1:
                    return (
                        500,
                        {},
                        "https://u1:p1@assets.example.invalid/logo.svg"
                        "?X-Amz-Signature=SYNTHETIC_SET_SIG "
                        '"data:image/svg+xml,<svg>SYNTHETIC_SET_SVG</svg>"',
                    )
                return (
                    502,
                    {},
                    "reset https://u2:p2@assets.example.invalid/reset.svg"
                    "?access_token=SYNTHETIC_RESET_TOK",
                )
            self.fail(f"Unexpected smoke request: {path}")

        with (
            mock.patch.object(self.smoke, "wait_for_ingress_ping"),
            mock.patch.object(
                self.smoke,
                "make_http_client",
                side_effect=[([], guest), (jar, authenticated)],
            ),
            mock.patch.object(
                self.smoke,
                "http_call_with_cookies",
                return_value=(
                    200,
                    {},
                    [f"sid={sid}; HttpOnly; Path=/; SameSite=Lax"],
                    "{}",
                ),
            ),
            mock.patch.object(self.smoke, "http_call", side_effect=http),
            self.assertRaises(AssertionError) as caught,
        ):
            self.smoke.run_http_and_socketio_smoke(
                "unused-project",
                Path("unused.env"),
                18080,
                "synthetic.localhost",
                "unused",
                logger,
            )

        exc_msg = str(caught.exception)
        rendered = logger.render()
        self.assertIn("Branding update failed (500):", exc_msg)
        self.assertIn("CRM branding smoke failed before branding reset:", rendered)
        self.assertIn(
            "CRM branding reset in finally failed after primary error:", rendered
        )
        self.assertIn(
            '"expected_icon_href": "/assets/crm_cskh/cskh/logo.svg?<REDACTED_QUERY:sha256=',
            rendered,
        )
        for leak in (
            "u1",
            "p1",
            "u2",
            "p2",
            "SYNTHETIC_SET_SIG",
            "SYNTHETIC_SET_SVG",
            "SYNTHETIC_RESET_TOK",
            "fav=smoke-v1",
        ):
            self.assertNotIn(leak, exc_msg)
            self.assertNotIn(leak, rendered)

    def test_secondary_reset_failure_does_not_mask_primary_transport_or_favicon_error(
        self,
    ):
        logger = self.smoke.SmokeLogger()
        guest, authenticated = object(), object()
        sid = "synthetic-session-for-reset-mask-test"
        admin_pw = "AdminSecretForMaskTest_12345"
        csrf = "fedcba9876543210fedcba9876543210"
        logger.register_secret(admin_pw, "<REDACTED_ADMIN_PASSWORD>")
        jar = [types.SimpleNamespace(name="sid", value=sid)]
        shell_base = (
            '<head>/assets/crm_cskh/cskh/cskh.css</head> class="cskh-sidebar" '
            f'Asia/Ho_Chi_Minh Tổng quan Chất lượng "X-Frappe-CSRF-Token": "{csrf}"'
        )
        v1_logo = "/assets/crm_cskh/cskh/logo.svg?brand=smoke-v1"
        v1_fav = "/assets/crm_cskh/cskh/logo.svg?fav=smoke-v1"
        v2_logo = "/assets/crm_cskh/cskh/logo.svg?brand=smoke-v2"
        v2_fav = "/assets/crm_cskh/cskh/logo.svg?fav=smoke-v2"
        phase_state = {"current": "init", "v2_calls": 0, "reset_snapshots": []}

        def http(opener, url, site, method="GET", data=None, headers=None):
            path = self.smoke.urllib.parse.urlsplit(url).path
            if opener is guest:
                return 403, {}, ""
            if path == "/api/method/frappe.auth.get_logged_user":
                return 200, {}, "Administrator"
            if path == "/cskh/quality-overview":
                return 200, {}, shell_base
            if path == "/cskh/dashboard":
                if phase_state["current"] == "v1":
                    return (
                        200,
                        {},
                        f'{shell_base} src="{v1_logo}" class="cskh-sidebar-logo is-brand-logo"',
                    )
                if phase_state["current"] == "v2":
                    return (
                        200,
                        {},
                        f'{shell_base} src="{v2_logo}" class="cskh-sidebar-logo is-brand-logo"',
                    )
                return 200, {}, shell_base
            if path == "/crm":
                return 200, {}, 'data-cskh-crm-theme="1" crm-sidebar-bridge.js'
            if path == "/api/method/crm_cskh.api.p3_reporting.request_recompute":
                return 400, {}, ""
            if path == "/api/method/crm_cskh.api.health.ping":
                return (
                    (200, {}, '{"message":{"app":"crm_cskh"}}')
                    if (headers or {}).get("X-Frappe-CSRF-Token") == csrf
                    else (400, {}, "")
                )
            if path == "/api/method/frappe.client.set_value":
                fields = json.loads(data)["fieldname"]
                if fields["favicon"] == v1_fav:
                    phase_state["current"] = "v1"
                    return 200, {}, "{}"
                if fields["favicon"] == v2_fav:
                    phase_state["current"] = "v2"
                    return 200, {}, "{}"
                if fields["favicon"] == "":
                    phase_state["reset_snapshots"].append(logger.render())
                    return 503, {}, f"upstream down pwd={admin_pw} sid={sid}"
            if path == "/crm/leads":
                if phase_state["current"] == "v1":
                    return (
                        200,
                        {},
                        f'<link rel="icon" href="{v1_fav}"> id="hrpartner-branding-prepaint" {v1_logo}',
                    )
                phase_state["v2_calls"] += 1
                if phase_state["v2_calls"] == 1:
                    return (
                        200,
                        {},
                        f'<link rel="icon" href="{v2_fav}"> id="hrpartner-branding-prepaint" {v2_logo}',
                    )
                raise self.smoke.urllib.error.URLError(
                    f"connection reset sid={sid} adm={admin_pw}"
                )
            self.fail(f"Unexpected smoke request: {path}")

        with (
            mock.patch.object(self.smoke, "wait_for_ingress_ping"),
            mock.patch.object(
                self.smoke,
                "make_http_client",
                side_effect=[([], guest), (jar, authenticated)],
            ),
            mock.patch.object(
                self.smoke,
                "http_call_with_cookies",
                return_value=(
                    200,
                    {},
                    [f"sid={sid}; HttpOnly; Path=/; SameSite=Lax"],
                    "{}",
                ),
            ),
            mock.patch.object(self.smoke, "http_call", side_effect=http),
            self.assertRaises(AssertionError) as caught,
        ):
            self.smoke.run_http_and_socketio_smoke(
                "unused-project",
                Path("unused.env"),
                18080,
                "synthetic.localhost",
                admin_pw,
                logger,
            )

        exc_msg = str(caught.exception)
        self.assertTrue(exc_msg.startswith("CRM branding reload failed: "), exc_msg)
        detail = json.loads(exc_msg.split(": ", 1)[1])
        self.assertEqual(detail["phase"], "v2")
        self.assertEqual(detail["reload_index"], 2)
        self.assertIsNone(detail["http_status"])
        self.assertEqual(detail["failures"], ["http_transport_error"])
        self.assertIn("sid=<REDACTED_SID>", detail["error"])
        self.assertIn("<REDACTED_ADMIN_PASSWORD>", detail["error"])
        for secret_val in (sid, admin_pw):
            self.assertNotIn(secret_val, exc_msg)
            self.assertNotIn(secret_val, logger.render())
        self.assertEqual(len(phase_state["reset_snapshots"]), 1)
        self.assertIn(
            '"reload_index": 2',
            phase_state["reset_snapshots"][0],
        )
        self.assertIn(
            "CRM branding reset in finally failed after primary error:",
            logger.render(),
        )


if __name__ == "__main__":
    unittest.main()
