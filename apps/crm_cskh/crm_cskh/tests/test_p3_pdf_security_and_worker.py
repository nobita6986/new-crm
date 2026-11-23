"""Canonical P3/P4 JUnit integration tests for PDF SSRF/file/RBAC lockdown & worker hooks.

Closes `F-TRIAL-PDFSEC-01`, `F-TRIAL-INV-01`, and `F-TRIAL-HOOK-RB-01` in the gated
`p3_test_inventory.json` suite (`ci_junit_gate.py` per-method PASS enforcement):
  1. `wkhtmltopdf` HTML/CSS/header/footer SSRF, internal port, `302` redirect, and `file://` blocking
     while preserving Vietnamese diacritics, `DejaVu Sans` `/ToUnicode`, multi-page tables, and badge image.
  2. `WeasyPrint` `HTML` and `CSS` identical safe-fetch enforcement (blocking `filename=`, `file_obj=`,
     positional `guess`, external/internal `url=`, filesystem `base_url`, `@import`, `302` redirects).
  3. Protocol-relative URL (`//...`, `///...`, `\\\\...`) rejection in HTML and CSS across both engines.
  4. Path traversal (`..`, `%2e%2e`, `%252e%252e`) and symlink (`O_NOFOLLOW`) rejection across
     `public/files`, `private/files`, and `assets`.
  5. `/private/files/...` RBAC enforcing `Guest` denial, cross-scope `Sales User` denial, and
     `CRM Lead` reassignment revocation (`linked_lead_has_permission`, even when `file_doc.owner` matches).
  6. Unattached private `File` denial for non-system `Sales User` actors.
  7. Concurrent multi-threaded `ensure_pdf_runtime_hooks()` initialization safety (`threading.RLock`).
  8. Real Frappe `execute_job` (`before_job` hook) PDF generation & private-file RBAC enforcement.
"""

from __future__ import annotations

import http.server
import io
import json
import os
import subprocess
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.background_jobs import execute_job
from frappe.utils.pdf import get_pdf
from frappe.utils.weasyprint import PrintFormatGenerator, import_weasyprint
from PIL import Image
from pypdf import PdfReader

from crm_cskh.services import pdf_assets, pdf_hooks


def _make_png_bytes(width: int = 48, height: int = 48, color: tuple[int, int, int] = (24, 108, 192)) -> bytes:
	buf = io.BytesIO()
	img = Image.new("RGB", (width, height), color)
	for x in range(max(1, width // 4), max(2, (width * 3) // 4)):
		for y in range(max(1, height // 4), max(2, (height * 3) // 4)):
			img.putpixel((x, y), (242, 186, 40))
	img.save(buf, format="PNG")
	return buf.getvalue()


def _inspect_pdf(raw_pdf: bytes) -> dict[str, object]:
	reader = PdfReader(io.BytesIO(raw_pdf))
	page_texts = [unicodedata.normalize("NFC", pg.extract_text() or "") for pg in reader.pages]
	compact = "".join("".join(t.split()) for t in page_texts)

	fonts: list[str] = []
	tounicode = 0
	image_dims: list[tuple[int, int]] = []
	seen: set[int] = set()

	def _walk(res_obj) -> None:
		nonlocal tounicode
		if not res_obj:
			return
		res = res_obj.get_object() if hasattr(res_obj, "get_object") else res_obj
		oid = id(res)
		if oid in seen or not isinstance(res, dict):
			return
		seen.add(oid)
		fdict = res.get("/Font")
		if fdict:
			fdict = fdict.get_object() if hasattr(fdict, "get_object") else fdict
			for _, fref in fdict.items():
				fo = fref.get_object() if hasattr(fref, "get_object") else fref
				bf = str(fo.get("/BaseFont") or "")
				if bf:
					fonts.append(bf)
				if "/ToUnicode" in fo:
					tounicode += 1
		xdict = res.get("/XObject")
		if xdict:
			xdict = xdict.get_object() if hasattr(xdict, "get_object") else xdict
			for _, xref in xdict.items():
				xo = xref.get_object() if hasattr(xref, "get_object") else xref
				sub = str(xo.get("/Subtype") or "")
				if sub == "/Image":
					image_dims.append((int(xo.get("/Width") or 0), int(xo.get("/Height") or 0)))
				elif sub == "/Form":
					_walk(xo.get("/Resources"))

	for pg in reader.pages:
		_walk(pg.get("/Resources"))

	return {
		"valid": raw_pdf.startswith(b"%PDF-1.") and b"%%EOF" in raw_pdf[-128:],
		"page_count": len(reader.pages),
		"compact_text": compact,
		"fonts": sorted(set(fonts)),
		"dejavu": any("dejavu" in f.lower() for f in fonts),
		"tounicode": tounicode,
		"image_dims": image_dims,
	}


@contextmanager
def _ephemeral_ssrf_and_redirect_server():
	hits: list[str] = []
	png_payload = _make_png_bytes(60, 60, (200, 20, 20))

	class _ProbeHandler(http.server.BaseHTTPRequestHandler):
		def do_GET(self) -> None:
			hits.append(self.path)
			if self.path.startswith("/redirect"):
				self.send_response(302)
				self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ssrf.png")
				self.end_headers()
				return
			if self.path.endswith(".css"):
				body = b"body { color: rgb(255, 0, 0); }"
				self.send_response(200)
				self.send_header("Content-Type", "text/css")
				self.send_header("Content-Length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)
				return
			self.send_response(200)
			self.send_header("Content-Type", "image/png")
			self.send_header("Content-Length", str(len(png_payload)))
			self.end_headers()
			self.wfile.write(png_payload)

		def log_message(self, format: str, *args: object) -> None:
			return

	srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
	port = srv.server_address[1]
	th = threading.Thread(target=srv.serve_forever, daemon=True)
	th.start()
	try:
		yield f"http://127.0.0.1:{port}", hits
	finally:
		srv.shutdown()
		srv.server_close()


class TestP3PdfSecurityAndWorker(FrappeTestCase):
	"""Canonical JUnit-gated PDF security, RBAC, concurrency, and RQ worker suite."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		pdf_assets.ensure_pdf_runtime_hooks()
		cls.staff_a = "pdf.sec.a@cskh-test.local"
		cls.staff_b = "pdf.sec.b@cskh-test.local"
		for email, first_name in (
			(cls.staff_a, "Nhân viên PDF Bảo mật A"),
			(cls.staff_b, "Nhân viên PDF Bảo mật B"),
		):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": first_name,
						"enabled": 1,
						"user_type": "System User",
						"send_welcome_email": 0,
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)

		pub_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "cskh-ut-public-badge.png",
				"is_private": 0,
				"content": _make_png_bytes(48, 48, (18, 92, 168)),
			}
		).insert(ignore_permissions=True)
		cls.public_badge_url = pub_file.file_url
		cls.public_file_name = pub_file.name
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_wkhtmltopdf_blocks_ssrf_redirects_and_local_files_while_preserving_vietnamese_pdf(self):
		with _ephemeral_ssrf_and_redirect_server() as (probe_origin, hits):
			rows_html = "".join(
				f"<tr><td>{idx}</td><td>HỒ-SƠ-{idx:03d}</td><td>Kiểm định tiếng Việt Ắ Ầ Ẩ Ẫ Ậ Ứ Ừ Ử Ữ Ự</td></tr>"
				for idx in range(1, 75)
			)
			html = (
				"<html><head>"
				f'<base href="{probe_origin}/">'
				f'<meta http-equiv="refresh" content="0;url={probe_origin}/redirect">'
				f'<link rel="stylesheet" href="{probe_origin}/evil.css">'
				f'<style>@import "{probe_origin}/evil.css"; '
				f'.print-format {{ font-family: "DejaVu Sans", sans-serif; background-image: url("{probe_origin}/redirect"); }} '
				'.local-leak { background-image: url("file:///etc/passwd"); }</style>'
				"</head><body>"
				'<div id="header-html"><div>TIÊU ĐỀ ĐẦU TRANG CSKH'
				f'<img src="{probe_origin}/redirect"/></div></div>'
				"<h2>BÁO CÁO CHẤT LƯỢNG DỊCH VỤ CSKH — ĐẶNG QUỐC CƯỜNG</h2>"
				f'<p><img src="{self.public_badge_url}" width="48" height="48"/></p>'
				f'<p><img src="{probe_origin}/ssrf.png"/></p>'
				f'<p><img src="{probe_origin}/redirect"/></p>'
				'<p><img src="file:///etc/hosts"/></p>'
				f'<iframe src="{probe_origin}/ssrf.png"></iframe>'
				f"<table><thead><tr><th>STT</th><th>Mã</th><th>Nội dung</th></tr></thead><tbody>{rows_html}</tbody></table>"
				'<div id="footer-html"><div>CHÂN TRANG BẢO MẬT CSKH</div></div>'
				"</body></html>"
			)
			raw_pdf = get_pdf(html)
			self.assertEqual(hits, [], f"wkhtmltopdf emitted unexpected network/redirect hits: {hits}")

		info = _inspect_pdf(raw_pdf)
		self.assertTrue(info["valid"])
		self.assertGreaterEqual(int(info["page_count"]), 2)
		self.assertTrue(info["dejavu"])
		self.assertGreaterEqual(int(info["tounicode"]), 1)
		self.assertIn((48, 48), info["image_dims"])
		self.assertNotIn((60, 60), info["image_dims"])
		compact = str(info["compact_text"])
		self.assertIn("BÁOCÁOCHẤTLƯỢNGDỊCHVỤCSKH", compact)
		self.assertIn("ĐẶNGQUỐCCƯỜNG", compact)
		self.assertIn("TIÊUĐỀĐẦUTRANGCSKH", compact)
		self.assertNotIn("root:x:0:0", compact)

	def test_weasyprint_html_and_css_enforce_identical_safe_fetch_and_block_redirects_and_files(self):
		import weasyprint

		html_cls, css_cls = import_weasyprint()
		with _ephemeral_ssrf_and_redirect_server() as (probe_origin, hits):
			# Direct file / guess / non-local URL inputs MUST fail closed on both HTML and CSS
			for bad_call in (
				lambda: html_cls("/etc/hosts"),
				lambda: html_cls(filename="/etc/hosts"),
				lambda: html_cls(file_obj=io.StringIO("<p>x</p>")),
				lambda: html_cls(url=f"{probe_origin}/ssrf.png"),
				lambda: css_cls("/etc/hosts"),
				lambda: css_cls(filename="/etc/hosts"),
				lambda: css_cls(file_obj=io.StringIO("body {}")),
				lambda: css_cls(url=f"{probe_origin}/evil.css"),
				lambda: weasyprint.HTML(filename="/etc/hosts"),
				lambda: weasyprint.CSS(filename="/etc/hosts"),
			):
				with self.assertRaises(ValueError):
					bad_call()

			# HTML + CSS with @import, url(...), redirect, and filesystem base_url MUST emit zero hits
			html_doc = html_cls(
				string=(
					'<html><head><style>@import "'
					+ f"{probe_origin}/evil.css"
					+ '"; body { font-family: "DejaVu Sans", sans-serif; }</style></head><body>'
					"<p>Kiểm chứng WeasyPrint Tiếng Việt Nguyễn Thị Ánh Tuyết</p>"
					f'<img src="{self.public_badge_url}" width="48" height="48"/>'
					f'<img src="{probe_origin}/redirect"/>'
					'<img src="file:///etc/passwd"/>'
					"</body></html>"
				),
				base_url="/etc/",
			)
			user_sheet = css_cls(
				string=(
					f'@import url("{probe_origin}/redirect"); '
					f'body {{ background-image: url("{probe_origin}/ssrf.png"); }} '
					'p { background-image: url("file:///etc/hosts"); }'
				),
				base_url="file:///etc/",
			)
			raw_pdf = html_doc.write_pdf(stylesheets=[user_sheet])
			self.assertEqual(hits, [], f"WeasyPrint emitted unexpected network/redirect hits: {hits}")

		info = _inspect_pdf(raw_pdf)
		self.assertTrue(info["valid"])
		self.assertTrue(info["dejavu"])
		self.assertIn((48, 48), info["image_dims"])
		self.assertNotIn((60, 60), info["image_dims"])
		self.assertIn("NguyễnThịÁnhTuyết", str(info["compact_text"]))

	def test_protocol_relative_urls_blocked_in_html_and_css_across_both_engines(self):
		html_cls, css_cls = import_weasyprint()
		site_host = str(frappe.local.site)
		badge_rel = self.public_badge_url

		# Protocol-relative URLs (`//...`, `///...`, `\\...`) must NOT resolve even when host matches site
		for proto_rel in (
			f"//evil.example.invalid{badge_rel}",
			f"//{site_host}{badge_rel}",
			f"///files/{Path(badge_rel).name}",
			f"\\\\{site_host}\\files\\{Path(badge_rel).name}",
		):
			self.assertIsNone(pdf_assets._resolve_same_site_resource(proto_rel, include_private_files=True))

		probe_html = (
			"<html><head><style>"
			f'@import "//{site_host}/assets/frappe/css/nonexistent.css"; '
			f'body {{ font-family: "DejaVu Sans", sans-serif; background-image: url("//{site_host}{badge_rel}"); }}'
			"</style></head><body>"
			f'<img src="//evil.example.invalid{badge_rel}"/>'
			f'<img src="//{site_host}{badge_rel}"/>'
			'<img src="///etc/passwd"/>'
			"</body></html>"
		)
		wk_pdf = get_pdf(probe_html)
		wp_pdf = html_cls(string=probe_html).write_pdf(
			stylesheets=[css_cls(string=f'div {{ background: url("//evil.example.invalid{badge_rel}"); }}')]
		)
		self.assertNotIn((48, 48), _inspect_pdf(wk_pdf)["image_dims"])
		self.assertNotIn((48, 48), _inspect_pdf(wp_pdf)["image_dims"])

	def test_path_traversal_and_symlinks_rejected_for_public_private_and_assets(self):
		pub_dir = Path(frappe.get_site_path("public", "files"))
		priv_dir = Path(frappe.get_site_path("private", "files"))
		pub_dir.mkdir(parents=True, exist_ok=True)
		priv_dir.mkdir(parents=True, exist_ok=True)
		site_cfg = Path(frappe.get_site_path("site_config.json")).resolve()

		pub_sym = pub_dir / "ut-pub-symlink.png"
		priv_sym = priv_dir / "ut-priv-symlink.png"
		priv_file_doc = None
		try:
			for sym in (pub_sym, priv_sym):
				if os.path.lexists(sym):
					os.unlink(sym)
			os.symlink(site_cfg, pub_sym)
			os.symlink(site_cfg, priv_sym)

			# Even if a DB File row exists pointing at a symlink in private/files, O_NOFOLLOW must reject it
			priv_file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "ut-priv-symlink.png",
					"file_url": "/private/files/ut-priv-symlink.png",
					"is_private": 1,
				}
			)
			priv_file_doc.db_insert()

			for attack_url in (
				"/files/../site_config.json",
				"/files/%2e%2e/site_config.json",
				"/files/%252e%252e/site_config.json",
				"/private/files/../site_config.json",
				"/private/files/%2e%2e/site_config.json",
				"/assets/frappe/../../sites/common_site_config.json",
				"/assets/%2e%2e/sites/common_site_config.json",
				"/files/ut-pub-symlink.png",
				"/private/files/ut-priv-symlink.png",
			):
				self.assertIsNone(
					pdf_assets._resolve_same_site_resource(attack_url, include_private_files=True),
					f"Traversal/symlink URL was not blocked: {attack_url}",
				)
		finally:
			if priv_file_doc and frappe.db.exists("File", priv_file_doc.name):
				frappe.db.delete("File", {"name": priv_file_doc.name})
			for sym in (pub_sym, priv_sym):
				if os.path.lexists(sym):
					os.unlink(sym)

	def test_private_file_rbac_enforces_lead_scope_and_reassignment_on_both_engines(self):
		frappe.set_user("Administrator")
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Khách hàng",
				"last_name": "Riêng tư RBAC",
				"lead_owner": self.staff_a,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.staff_a)
		priv_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "cskh-ut-lead-private.png",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead.name,
				"content": _make_png_bytes(52, 52, (20, 140, 80)),
			}
		).insert(ignore_permissions=True)
		priv_url = priv_file.file_url
		self.assertTrue(priv_url.startswith("/private/files/"))

		html_cls, _ = import_weasyprint()
		tpl = (
			"<html><body style=\"font-family: 'DejaVu Sans', sans-serif;\">"
			f'<p>Hồ sơ đính kèm riêng tư</p><img src="{priv_url}" width="52" height="52"/>'
			"</body></html>"
		)

		try:
			# 1. Guest -> DENIED on both engines
			frappe.set_user("Guest")
			self.assertNotIn((52, 52), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertNotIn((52, 52), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])

			# 2. Out-of-scope staff_b -> DENIED on both engines
			frappe.set_user(self.staff_b)
			self.assertNotIn((52, 52), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertNotIn((52, 52), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])

			# 3. Current Lead owner staff_a -> ALLOWED on both engines
			frappe.set_user(self.staff_a)
			self.assertIn((52, 52), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertIn((52, 52), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])

			# 4. Reassign CRM Lead to staff_b while file_doc.owner remains staff_a:
			#    former owner staff_a MUST be revoked (`linked_lead_has_permission`), staff_b granted!
			frappe.set_user("Administrator")
			lead.reload()
			lead.lead_owner = self.staff_b
			lead.save(ignore_permissions=True)

			frappe.set_user(self.staff_a)
			self.assertEqual(frappe.db.get_value("File", priv_file.name, "owner"), self.staff_a)
			self.assertFalse(frappe.has_permission("File", "read", doc=priv_file, user=self.staff_a))
			self.assertNotIn((52, 52), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertNotIn((52, 52), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])

			frappe.set_user(self.staff_b)
			self.assertTrue(frappe.has_permission("File", "read", doc=priv_file, user=self.staff_b))
			self.assertIn((52, 52), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertIn((52, 52), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("File", priv_file.name, force=True, ignore_permissions=True)
			frappe.delete_doc("CRM Lead", lead.name, force=True, ignore_permissions=True)

	def test_unattached_private_file_denied_to_non_system_sales_user(self):
		frappe.set_user(self.staff_a)
		unattached_priv = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "cskh-ut-unattached-private.png",
				"is_private": 1,
				"content": _make_png_bytes(44, 44, (120, 40, 140)),
			}
		).insert(ignore_permissions=True)
		html_cls, _ = import_weasyprint()
		tpl = f'<html><body><img src="{unattached_priv.file_url}" width="44" height="44"/></body></html>'
		try:
			# Sales User cannot bypass linked_lead_has_permission via unattached private File
			frappe.set_user(self.staff_a)
			self.assertNotIn((44, 44), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertNotIn((44, 44), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])

			# System Administrator is permitted
			frappe.set_user("Administrator")
			self.assertIn((44, 44), _inspect_pdf(get_pdf(tpl))["image_dims"])
			self.assertIn((44, 44), _inspect_pdf(html_cls(string=tpl).write_pdf())["image_dims"])
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("File", unattached_priv.name, force=True, ignore_permissions=True)

	def test_concurrent_multithreaded_hook_initialization_is_idempotent_and_race_free(self):
		import frappe.utils.pdf as frappe_pdf
		import frappe.utils.weasyprint as frappe_weasyprint

		barrier = threading.Barrier(16)
		before_wrap_count = pdf_assets._WRAP_INSTALL_COUNT

		# Simulate uninitialized flag under concurrent 16-thread contention
		with pdf_assets._HOOKS_LOCK:
			pdf_assets._HOOKS_INSTALLED = False

		def _worker(_idx: int) -> bool:
			barrier.wait(timeout=10)
			pdf_assets.ensure_pdf_runtime_hooks()
			return bool(pdf_assets._HOOKS_INSTALLED)

		with ThreadPoolExecutor(max_workers=16) as pool:
			results = list(pool.map(_worker, range(16)))

		self.assertEqual(results, [True] * 16)
		# Already-wrapped callables must NEVER be wrapped a second time
		self.assertEqual(pdf_assets._WRAP_INSTALL_COUNT, before_wrap_count)
		self.assertTrue(getattr(frappe_pdf.pdfkit.from_string, "_cskh_pdf_wrapped", False))
		self.assertTrue(getattr(frappe_pdf._get_base64_image, "_cskh_pdf_wrapped", False))
		self.assertTrue(getattr(frappe_weasyprint.import_weasyprint, "_cskh_pdf_wrapped", False))

	def test_background_job_execute_job_runs_before_job_hook_and_renders_safe_pdfs(self):
		before_job_hooks = list(frappe.get_hooks("before_job") or [])
		before_req_hooks = list(frappe.get_hooks("before_request") or [])
		before_test_hooks = list(frappe.get_hooks("before_tests") or [])
		on_print_hooks = list(frappe.get_hooks("on_print_pdf") or [])
		self.assertIn("crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks", before_job_hooks)
		self.assertIn("crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks", before_req_hooks)
		self.assertIn("crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks", before_test_hooks)
		self.assertIn("crm_cskh.services.pdf_hooks.on_print_pdf", on_print_hooks)

		frappe.set_user("Administrator")
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Trần Văn",
				"last_name": "Worker Nền",
				"organization": "Công ty Kiểm thử RQ Worker",
				"lead_owner": self.staff_a,
			}
		).insert(ignore_permissions=True)
		priv_badge = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "cskh-ut-worker-priv.png",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead.name,
				"content": _make_png_bytes(36, 36, (30, 120, 160)),
			}
		).insert(ignore_permissions=True)
		pf_wk = "CSKH UT Worker Wkhtmltopdf"
		pf_wp = "CSKH UT Worker WeasyPrint"
		for pf_name in (pf_wk, pf_wp):
			if frappe.db.exists("Print Format", pf_name):
				frappe.delete_doc("Print Format", pf_name, force=True, ignore_permissions=True)

		body_jinja = (
			"<div style=\"font-family: 'DejaVu Sans', sans-serif;\">"
			"<h2>BÁO CÁO CHẤT LƯỢNG DỊCH VỤ CSKH</h2>"
			"<p>{{ doc.first_name }} {{ doc.last_name }} — {{ doc.organization }}</p>"
			f'<img src="{self.public_badge_url}" width="48" height="48"/>'
			"</div>"
		)
		frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": pf_wk,
				"doc_type": "CRM Lead",
				"module": "FCRM",
				"standard": "No",
				"custom_format": 1,
				"print_format_type": "Jinja",
				"pdf_generator": "wkhtmltopdf",
				"html": body_jinja,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": pf_wp,
				"doc_type": "CRM Lead",
				"module": "FCRM",
				"standard": "No",
				"custom_format": 0,
				"print_format_builder_beta": 1,
				"margin_top": 15,
				"margin_bottom": 15,
				"margin_left": 15,
				"margin_right": 15,
				"font_size": 10,
				"css": "body { font-family: 'DejaVu Sans', sans-serif !important; }",
				"format_data": frappe.as_json(
					{
						"header": "",
						"footer": "",
						"sections": [
							{
								"label": "",
								"page_break": False,
								"columns": [
									{
										"fields": [
											{
												"label": "Custom HTML",
												"fieldname": "custom_html",
												"fieldtype": "HTML",
												"html": body_jinja,
											}
										]
									}
								],
							}
						],
					}
				),
			}
		).insert(ignore_permissions=True)

		cache_key = f"cskh:ut:worker_pdf:{frappe.generate_hash(length=8)}"
		try:
			with pdf_assets._HOOKS_LOCK:
				pdf_assets._HOOKS_INSTALLED = False

			execute_job(
				site=frappe.local.site,
				method="crm_cskh.services.pdf_assets.run_rq_pdf_verification_job",
				event=None,
				job_name="ut-pdf-worker-probe",
				kwargs={
					"doctype": "CRM Lead",
					"name": lead.name,
					"wkhtmltopdf_format": pf_wk,
					"weasyprint_format": pf_wp,
					"authorized_user": self.staff_a,
					"unauthorized_user": self.staff_b,
					"private_file_url": priv_badge.file_url,
					"result_cache_key": cache_key,
				},
				user="Administrator",
				is_async=False,
			)
			cached = frappe.cache.get_value(cache_key)
			self.assertIsInstance(cached, dict)
			self.assertEqual(cached["status"], "PASS")
			self.assertTrue(cached["hooks_preinstalled_by_before_job"])
			for eng in ("wkhtmltopdf", "weasyprint"):
				self.assertTrue(cached[eng]["valid_pdf_envelope"], eng)
				self.assertTrue(cached[eng]["dejavu_font_embedded"], eng)
				self.assertTrue(cached[eng]["contains_vietnamese_title"], eng)
				self.assertGreaterEqual(cached[eng]["image_xobject_count"], 1, eng)
			probe = cached["private_file_and_ssrf_probe"]
			self.assertEqual(probe["authorized_large_images_wk"], 1)
			self.assertEqual(probe["authorized_large_images_wp"], 1)
			self.assertEqual(probe["unauthorized_large_images_wk"], 0)
			self.assertEqual(probe["unauthorized_large_images_wp"], 0)
		finally:
			frappe.set_user("Administrator")
			frappe.cache.delete_value(cache_key)
			for dt, dn in (
				("Print Format", pf_wk),
				("Print Format", pf_wp),
				("File", priv_badge.name),
				("CRM Lead", lead.name),
			):
				if frappe.db.exists(dt, dn):
					frappe.delete_doc(dt, dn, force=True, ignore_permissions=True)
			PrintFormatGenerator.get_element  # keep import active

	def test_non_pdf_jobs_in_fresh_process_do_not_load_pdf_engines_and_measure_fast_hook(self):
		"""Real fresh child processes executing non-PDF jobs must NOT load WeasyPrint or PDF engines."""
		sites_dir = str(Path(getattr(frappe.local, "sites_path", ".")).resolve())
		site_name = str(frappe.local.site)

		probe_code = r"""
import json, os, sys, time
import frappe
from frappe.utils.background_jobs import execute_job

site = sys.argv[1]
sites_dir = sys.argv[2]
os.chdir(sites_dir)
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    hooks = list(frappe.get_hooks("before_job") or [])
    timings = []
    for h in hooks:
        t0 = time.perf_counter()
        fn = frappe.get_attr(h)
        t1 = time.perf_counter()
        fn()
        t2 = time.perf_counter()
        timings.append({
            "hook": h,
            "import_ms": round((t1 - t0) * 1000.0, 3),
            "call_ms": round((t2 - t1) * 1000.0, 3),
            "total_ms": round((t2 - t0) * 1000.0, 3),
        })

    for method_path, jname in (
        ("frappe.ping", "ut-nonpdf-ping"),
        ("crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations", "ut-nonpdf-sla"),
    ):
        execute_job(
            site=site,
            method=method_path,
            event=None,
            job_name=jname,
            kwargs={},
            user="Administrator",
            is_async=False,
        )

    from crm_cskh.services import pdf_hooks
    monitored = (
        "weasyprint",
        "frappe.utils.weasyprint",
        "frappe.utils.pdf",
        "pdfkit",
        "cssutils",
        "pypdf",
        "bs4",
        "crm_cskh.services.pdf_assets",
    )
    print("SUBPROC_NONPDF_JSON:" + json.dumps({
        "before_job_hooks": hooks,
        "timings": timings,
        "guard_armed": pdf_hooks.is_pdf_hook_guard_installed(),
        "loaded_after_non_pdf_jobs": {m: (m in sys.modules) for m in monitored},
    }))
finally:
    frappe.destroy()
"""
		proc = subprocess.run(
			[sys.executable, "-c", probe_code, site_name, sites_dir],
			capture_output=True,
			text=True,
			timeout=60,
			check=False,
		)
		self.assertEqual(
			proc.returncode, 0, f"Fresh non-PDF subprocess failed:\n{proc.stderr}\n{proc.stdout}"
		)
		marker = [
			ln[len("SUBPROC_NONPDF_JSON:") :]
			for ln in proc.stdout.splitlines()
			if ln.startswith("SUBPROC_NONPDF_JSON:")
		]
		self.assertTrue(marker, proc.stdout)
		data = json.loads(marker[-1])
		self.assertTrue(data["guard_armed"])
		self.assertIn("crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks", data["before_job_hooks"])
		for mod_name, was_loaded in data["loaded_after_non_pdf_jobs"].items():
			self.assertFalse(was_loaded, f"Non-PDF job eagerly imported {mod_name}: {data}")
		pdf_hook_timings = [
			t for t in data["timings"] if t["hook"] == "crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks"
		]
		self.assertEqual(len(pdf_hook_timings), 1)
		self.assertLess(float(pdf_hook_timings[0]["total_ms"]), 250.0, pdf_hook_timings[0])
		self.assertTrue(pdf_hooks.is_pdf_hook_guard_installed())

	def test_first_pdf_call_in_fresh_process_enforces_security_across_http_rq_and_cli_paths(self):
		"""First PDF engine call in a fresh process must enforce full SSRF/file/RBAC protections."""
		sites_dir = Path(getattr(frappe.local, "sites_path", ".")).resolve()
		bench_root = sites_dir.parent
		site_name = str(frappe.local.site)

		frappe.set_user("Administrator")
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Lê Hoàng",
				"last_name": "Khởi Tạo Lười",
				"lead_owner": self.staff_a,
			}
		).insert(ignore_permissions=True)
		priv_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "cskh-ut-lazy-first-call-priv.png",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead.name,
				"content": _make_png_bytes(52, 52, (25, 130, 90)),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		try:
			with _ephemeral_ssrf_and_redirect_server() as (probe_origin, hits):
				# Path A: Fresh process -> first `from weasyprint import HTML, CSS` & `from frappe.utils.pdf import get_pdf`
				first_use_py = r"""
import io, json, os, sys
import frappe
from pypdf import PdfReader

cfg = json.loads(sys.stdin.read())
os.chdir(cfg["sites_dir"])
frappe.init(site=cfg["site_name"], sites_path=".")
frappe.connect()
try:
    # Simulate HTTP before_request / RQ before_job arming lazy hook before any PDF import
    for h in (frappe.get_hooks("before_request") or []):
        if "pdf_hooks" in h:
            frappe.get_attr(h)()

    pre_loaded = {
        k: (k in sys.modules)
        for k in ("weasyprint", "frappe.utils.weasyprint", "frappe.utils.pdf", "pdfkit")
    }

    # 1. Direct `from weasyprint import HTML, CSS` must receive ALREADY-WRAPPED safe factories!
    from weasyprint import CSS, HTML
    wp_wrapped = bool(getattr(HTML, "_cskh_pdf_wrapped", False) and getattr(CSS, "_cskh_pdf_wrapped", False))
    wp_file_blocked = False
    try:
        HTML(filename="/etc/hosts")
    except ValueError:
        wp_file_blocked = True

    # 2. Direct `import pdfkit` + `from frappe.utils.pdf import get_pdf` must block from_url & SSRF on 1st call!
    import pdfkit
    from frappe.utils.pdf import get_pdf
    pdfkit_url_blocked = False
    try:
        pdfkit.from_url(cfg["probe_origin"] + "/ssrf.png", False)
    except PermissionError:
        pdfkit_url_blocked = True

    tpl = (
        '<html><head><style>@import "' + cfg["probe_origin"] + '/evil.css"; '
        'body { font-family: "DejaVu Sans", sans-serif; }</style></head><body>'
        '<p>Kiểm định lần gọi PDF đầu tiên</p>'
        '<img src="' + cfg["priv_url"] + '" width="52" height="52"/>'
        '<img src="' + cfg["probe_origin"] + '/redirect"/>'
        '<img src="//evil.example.invalid/files/p.png"/>'
        '<img src="file:///etc/passwd"/>'
        '</body></html>'
    )

    def _has_52(pdf_bytes):
        rdr = PdfReader(io.BytesIO(pdf_bytes))
        for p in rdr.pages:
            res = p.get("/Resources")
            res = res.get_object() if hasattr(res, "get_object") else (res or {})
            xd = res.get("/XObject")
            xd = xd.get_object() if hasattr(xd, "get_object") else (xd or {})
            for _, xr in xd.items():
                xo = xr.get_object() if hasattr(xr, "get_object") else xr
                if str(xo.get("/Subtype") or "") == "/Image" and int(xo.get("/Width") or 0) == 52:
                    return True
        return False

    frappe.set_user(cfg["staff_b"])
    b_wk = _has_52(get_pdf(tpl))
    b_wp = _has_52(HTML(string=tpl, base_url="/etc/").write_pdf(
        stylesheets=[CSS(string='@import "' + cfg["probe_origin"] + '/redirect";', base_url="file:///etc/")]
    ))

    frappe.set_user(cfg["staff_a"])
    a_wk = _has_52(get_pdf(tpl))
    a_wp = _has_52(HTML(string=tpl).write_pdf())

    print("FIRST_USE_JSON:" + json.dumps({
        "pre_loaded": pre_loaded,
        "wp_wrapped": wp_wrapped,
        "wp_file_blocked": wp_file_blocked,
        "pdfkit_url_blocked": pdfkit_url_blocked,
        "staff_b_wk_has_priv": b_wk,
        "staff_b_wp_has_priv": b_wp,
        "staff_a_wk_has_priv": a_wk,
        "staff_a_wp_has_priv": a_wp,
    }))
finally:
    frappe.destroy()
"""
				p1 = subprocess.run(
					[sys.executable, "-c", first_use_py],
					input=json.dumps(
						{
							"sites_dir": str(sites_dir),
							"site_name": site_name,
							"probe_origin": probe_origin,
							"priv_url": priv_file.file_url,
							"staff_a": self.staff_a,
							"staff_b": self.staff_b,
						}
					),
					capture_output=True,
					text=True,
					timeout=90,
					check=False,
				)
				self.assertEqual(p1.returncode, 0, f"First-use subprocess failed:\n{p1.stderr}\n{p1.stdout}")
				m1 = [
					ln[len("FIRST_USE_JSON:") :]
					for ln in p1.stdout.splitlines()
					if ln.startswith("FIRST_USE_JSON:")
				]
				self.assertTrue(m1, p1.stdout)
				r1 = json.loads(m1[-1])
				self.assertEqual(
					r1["pre_loaded"],
					{
						"weasyprint": False,
						"frappe.utils.weasyprint": False,
						"frappe.utils.pdf": False,
						"pdfkit": False,
					},
				)
				self.assertTrue(r1["wp_wrapped"])
				self.assertTrue(r1["wp_file_blocked"])
				self.assertTrue(r1["pdfkit_url_blocked"])
				self.assertFalse(r1["staff_b_wk_has_priv"])
				self.assertFalse(r1["staff_b_wp_has_priv"])
				self.assertTrue(r1["staff_a_wk_has_priv"])
				self.assertTrue(r1["staff_a_wp_has_priv"])
				self.assertEqual(hits, [], f"First-use subprocess leaked SSRF/redirect requests: {hits}")

				# Path B: Supported CLI `bench --site <site> execute frappe.utils.pdf.get_pdf`
				cli_html = (
					f'<html><body><p>CLI PDF</p><img src="{probe_origin}/ssrf.png"/>'
					f'<img src="{probe_origin}/redirect"/><img src="file:///etc/hosts"/></body></html>'
				)
				p_cli = subprocess.run(
					[
						sys.executable,
						"-m",
						"frappe.utils.bench_helper",
						"frappe",
						"--site",
						site_name,
						"execute",
						"frappe.utils.pdf.get_pdf",
						"--args",
						json.dumps([cli_html]),
					],
					cwd=str(sites_dir),
					env={
						**os.environ,
						"PYTHONPATH": os.pathsep.join(
							[
								str(bench_root / "apps" / "frappe"),
								str(bench_root / "apps" / "crm"),
								str(bench_root / "apps" / "crm_cskh"),
							]
						),
					},
					capture_output=True,
					text=True,
					timeout=60,
					check=False,
				)
				self.assertEqual(
					p_cli.returncode,
					0,
					f"CLI bench execute get_pdf failed:\n{p_cli.stderr}\n{p_cli.stdout}",
				)
				self.assertEqual(hits, [], f"CLI bench execute leaked SSRF/redirect requests: {hits}")
		finally:
			frappe.set_user("Administrator")
			for dt, dn in (("File", priv_file.name), ("CRM Lead", lead.name)):
				if frappe.db.exists(dt, dn):
					frappe.delete_doc(dt, dn, force=True, ignore_permissions=True)
			frappe.db.commit()

	def test_subsequent_and_concurrent_first_time_pdf_calls_in_fresh_process_work_cleanly(self):
		"""Concurrent multi-threaded first-time + subsequent PDF calls in a fresh process must be race-free."""
		sites_dir = str(Path(getattr(frappe.local, "sites_path", ".")).resolve())
		site_name = str(frappe.local.site)

		with _ephemeral_ssrf_and_redirect_server() as (probe_origin, hits):
			concurrent_py = r"""
import json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor
import frappe

site = sys.argv[1]
sites_dir = sys.argv[2]
probe_origin = sys.argv[3]
os.chdir(sites_dir)

from crm_cskh.services import pdf_hooks
pdf_hooks.ensure_pdf_runtime_hooks()
assert "weasyprint" not in sys.modules
assert "frappe.utils.pdf" not in sys.modules

barrier = threading.Barrier(8)
html_doc = (
    '<html><body style="font-family: \'DejaVu Sans\', sans-serif;">'
    '<p>Kiểm thử đa luồng đồng thời Đặng Quốc Cường</p>'
    f'<img src="{probe_origin}/ssrf.png"/>'
    '</body></html>'
)

def _worker(idx: int) -> dict:
    frappe.init(site=site, sites_path=".")
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        barrier.wait(timeout=15)
        if idx % 2 == 0:
            from frappe.utils.pdf import get_pdf
            b1 = get_pdf(html_doc)
            b2 = get_pdf(html_doc)
        else:
            from frappe.utils.weasyprint import import_weasyprint
            h_cls, c_cls = import_weasyprint()
            b1 = h_cls(string=html_doc).write_pdf(stylesheets=[c_cls(string="@page { size: A4; }")])
            b2 = h_cls(string=html_doc).write_pdf()
        return {
            "ok_first": b1.startswith(b"%PDF-1.") and b"%%EOF" in b1[-128:],
            "ok_second": b2.startswith(b"%PDF-1.") and b"%%EOF" in b2[-128:],
        }
    finally:
        frappe.destroy()

with ThreadPoolExecutor(max_workers=8) as pool:
    outcomes = list(pool.map(_worker, range(8)))

from crm_cskh.services import pdf_assets
print("CONC_FIRST_JSON:" + json.dumps({
    "outcomes": outcomes,
    "wrap_install_count": pdf_assets._WRAP_INSTALL_COUNT,
}))
"""
			proc = subprocess.run(
				[sys.executable, "-c", concurrent_py, site_name, sites_dir, probe_origin],
				capture_output=True,
				text=True,
				timeout=90,
				check=False,
			)
			self.assertEqual(
				proc.returncode,
				0,
				f"Concurrent first-use subprocess failed:\n{proc.stderr}\n{proc.stdout}",
			)
			marker = [
				ln[len("CONC_FIRST_JSON:") :]
				for ln in proc.stdout.splitlines()
				if ln.startswith("CONC_FIRST_JSON:")
			]
			self.assertTrue(marker, proc.stdout)
			payload = json.loads(marker[-1])
			self.assertEqual(len(payload["outcomes"]), 8)
			for item in payload["outcomes"]:
				self.assertTrue(item["ok_first"], payload)
				self.assertTrue(item["ok_second"], payload)
			self.assertEqual(payload["wrap_install_count"], 1, payload)
			self.assertEqual(hits, [], f"Concurrent PDF workers leaked SSRF hits: {hits}")
