"""Hermetic local-asset resolution for Frappe v15 PDF engines (`wkhtmltopdf` & `WeasyPrint`).

In the hardened P4 Compose topology (`compose.cskh-common.yaml`), `backend` runs on
`app_net` + `data_net` (`internal: true`) and background RQ workers (`queue-short`,
`queue-long`) run exclusively on `data_net` (`internal: true`), without direct network
routes to external TLS ingress (`proxy` on `edge_net`).

Upstream Frappe `v15.126.0` (`53bae3ef2550ca96320f036f581446783cd11b72`):
  - `frappe.utils.pdf` (`wkhtmltopdf 0.12.6.1-3`) rewrites `/assets/...` and `/files/...`
    via `scrub_urls(html)` to `http(s)://<site_host>/...` while only base64-inlining
    private `/private/files/...` images (`inline_private_images`).
  - `frappe.utils.weasyprint` (`weasyprint==70.0`) passes `base_url = frappe.utils.get_url()`
    to `weasyprint.HTML(...)` and has a single-branch recursion bug in
    `PrintFormatGenerator.get_element` when `@page` margin boxes precede `<html>`.

This module resolves same-site `/assets/...` and `/files/...` stylesheets, fonts, and
images directly from the mounted `/home/frappe/frappe-bench/sites` filesystem (strictly
`realpath`-jailed against path traversal, while preserving Frappe `File` RBAC on
`/private/files/...` and blocking arbitrary external SSRF in `WeasyPrint`).
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import frappe
from bs4 import BeautifulSoup

_HOOKS_INSTALLED = False

_EXTRA_MIME_TYPES: dict[str, str] = {
	".css": "text/css",
	".gif": "image/gif",
	".ico": "image/x-icon",
	".jpeg": "image/jpeg",
	".jpg": "image/jpeg",
	".otf": "font/otf",
	".png": "image/png",
	".svg": "image/svg+xml",
	".ttf": "font/ttf",
	".webp": "image/webp",
	".woff": "font/woff",
	".woff2": "font/woff2",
}

_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.IGNORECASE)


def _guess_mime(path_or_name: str) -> str:
	ext = Path(path_or_name).suffix.lower()
	if ext in _EXTRA_MIME_TYPES:
		return _EXTRA_MIME_TYPES[ext]
	guessed, _ = mimetypes.guess_type(path_or_name)
	return guessed or "application/octet-stream"


def _allowed_same_site_hosts() -> set[str]:
	hosts = {"127.0.0.1", "localhost", "frontend", "backend"}
	site = getattr(frappe.local, "site", None)
	if site:
		hosts.add(str(site).strip().lower().split(":", 1)[0])
	req = getattr(frappe.local, "request", None)
	if req is not None:
		req_host = getattr(req, "host", "") or ""
		if req_host:
			hosts.add(str(req_host).strip().lower().split(":", 1)[0])
	try:
		site_url_host = urlparse(frappe.utils.get_url()).hostname
		if site_url_host:
			hosts.add(site_url_host.lower())
	except Exception:
		pass
	return {h for h in hosts if h}


def _sites_and_assets_roots() -> tuple[Path | None, Path | None, Path | None]:
	raw_sites = getattr(frappe.local, "sites_path", None) or "."
	sites_dir = Path(raw_sites)
	assets_link = sites_dir / "assets"
	assets_root = assets_link.resolve() if assets_link.exists() else None

	public_files_root: Path | None = None
	if getattr(frappe.local, "site", None):
		try:
			pf = Path(frappe.get_site_path("public", "files"))
			if pf.exists():
				public_files_root = pf.resolve()
		except Exception:
			public_files_root = None
	return sites_dir, assets_root, public_files_root


def _resolve_same_site_resource(
	raw_url: str,
	*,
	include_private_files: bool = False,
) -> tuple[bytes, str, Path | None] | None:
	"""Resolve a same-site `/assets/...` or `/files/...` URL safely inside jailed roots."""
	cleaned = (raw_url or "").strip()
	if not cleaned or cleaned.startswith(("data:", "mailto:", "tel:", "javascript:", "#", "about:")):
		return None

	parsed = urlparse(cleaned)
	if parsed.scheme:
		if parsed.scheme.lower() not in ("http", "https"):
			return None
		host = (parsed.hostname or "").lower()
		if host not in _allowed_same_site_hosts():
			return None

	url_path = unquote(parsed.path or "")
	if not url_path.startswith("/"):
		return None

	_, assets_root, public_files_root = _sites_and_assets_roots()

	if url_path.startswith("/assets/") and assets_root is not None:
		rel = url_path[len("/assets/") :].lstrip("/")
		if not rel:
			return None
		target = (assets_root / rel).resolve()
		if target.is_relative_to(assets_root) and target.is_file():
			return target.read_bytes(), _guess_mime(target.name), target
		return None

	if url_path.startswith("/files/") and public_files_root is not None:
		rel = url_path[len("/files/") :].lstrip("/")
		if not rel:
			return None
		target = (public_files_root / rel).resolve()
		if target.is_relative_to(public_files_root) and target.is_file():
			return target.read_bytes(), _guess_mime(target.name), target
		return None

	if include_private_files and url_path.startswith("/private/files/"):
		try:
			from frappe.core.doctype.file.utils import find_file_by_url

			file_doc = find_file_by_url(url_path)
			if not file_doc or not frappe.has_permission("File", "read", doc=file_doc):
				return None
			content = file_doc.get_content()
			if isinstance(content, str):
				content = content.encode("utf-8")
			return content, _guess_mime(url_path), None
		except Exception:
			return None

	return None


def _is_same_site_http_url(raw_url: str) -> bool:
	parsed = urlparse((raw_url or "").strip())
	if parsed.scheme.lower() not in ("http", "https"):
		return False
	return (parsed.hostname or "").lower() in _allowed_same_site_hosts()


def _inline_css_urls(css_text: str, *, css_file: Path | None = None) -> str:
	_, assets_root, _ = _sites_and_assets_roots()

	def _replace(match: re.Match[str]) -> str:
		raw_ref = (match.group(2) or "").strip()
		if not raw_ref or raw_ref.startswith(("data:", "#")):
			return match.group(0)

		resolved = _resolve_same_site_resource(raw_ref, include_private_files=False)
		if resolved is None and css_file is not None and assets_root is not None:
			parsed_ref = urlparse(raw_ref)
			if not parsed_ref.scheme and not (parsed_ref.path or "").startswith("/"):
				rel_part = unquote(parsed_ref.path or "")
				if rel_part:
					cand = (css_file.parent / rel_part).resolve()
					if cand.is_relative_to(assets_root) and cand.is_file():
						resolved = (cand.read_bytes(), _guess_mime(cand.name), cand)

		if resolved is not None:
			raw_bytes, mime, _ = resolved
			b64 = base64.b64encode(raw_bytes).decode("ascii")
			return f'url("data:{mime};base64,{b64}")'

		if _is_same_site_http_url(raw_ref):
			# Prevent wkhtmltopdf ConnectionRefusedError on missing local font variants
			return 'url("data:application/octet-stream;base64,")'

		return match.group(0)

	return _CSS_URL_RE.sub(_replace, css_text)


def inline_local_pdf_assets(html: str) -> str:
	"""Inline same-site `/assets/...` CSS & `/assets/...` + `/files/...` images as data URIs."""
	if not html:
		return html

	soup = BeautifulSoup(html, "html.parser")
	mutated = False

	for link_tag in list(soup.find_all("link")):
		rels = link_tag.get("rel") or []
		if isinstance(rels, str):
			rels = [rels]
		if not any(str(r).lower() == "stylesheet" for r in rels):
			continue
		href = str(link_tag.get("href") or "").strip()
		if not href:
			continue
		resolved = _resolve_same_site_resource(href, include_private_files=False)
		if resolved is not None:
			css_bytes, _, css_path = resolved
			css_text = css_bytes.decode("utf-8", errors="replace")
			inlined_css = _inline_css_urls(css_text, css_file=css_path)
			style_tag = soup.new_tag("style")
			style_tag["type"] = "text/css"
			style_tag.string = inlined_css
			link_tag.replace_with(style_tag)
			mutated = True
		elif _is_same_site_http_url(href):
			link_tag.decompose()
			mutated = True

	for style_tag in soup.find_all("style"):
		raw_css = style_tag.string
		if raw_css and "url(" in raw_css.lower():
			rewritten_css = _inline_css_urls(str(raw_css))
			if rewritten_css != raw_css:
				style_tag.string = rewritten_css
				mutated = True

	for img_tag in soup.find_all("img"):
		src = str(img_tag.get("src") or "").strip()
		if not src or src.startswith("data:"):
			continue
		resolved = _resolve_same_site_resource(src, include_private_files=True)
		if resolved is not None:
			img_bytes, mime, _ = resolved
			if mime.startswith("image/"):
				b64 = base64.b64encode(img_bytes).decode("ascii")
				img_tag["src"] = f"data:{mime};base64,{b64}"
				mutated = True
		elif _is_same_site_http_url(src):
			# Avoid aborting entire PDF on broken local image URL while leaving non-existent src harmless
			img_tag["src"] = "data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="
			mutated = True

	return str(soup) if mutated else html


def _cskh_get_element(boxes, element: str):
	"""Fixed depth-first element search for `frappe.utils.weasyprint.PrintFormatGenerator`."""
	for box in boxes:
		if getattr(box, "element_tag", None) == element:
			return box
		children = box.all_children() if hasattr(box, "all_children") else ()
		found = _cskh_get_element(children, element)
		if found is not None:
			return found
	return None


def _make_weasyprint_url_fetcher(default_fetcher):
	def _fetcher(url: str, timeout: int = 10, ssl_context=None):
		cleaned = (url or "").strip()
		if cleaned.startswith("data:"):
			return default_fetcher(cleaned, timeout=timeout, ssl_context=ssl_context)
		resolved = _resolve_same_site_resource(cleaned, include_private_files=True)
		if resolved is not None:
			raw_bytes, mime, _ = resolved
			return {"string": raw_bytes, "mime_type": mime}
		raise ValueError(f"Blocked non-local or missing PDF resource URL in isolated runtime: {cleaned}")

	return _fetcher


def ensure_pdf_runtime_hooks(*_args, **_kwargs) -> None:
	"""Idempotently install local-asset PDF adapters for `wkhtmltopdf` and `WeasyPrint`."""
	global _HOOKS_INSTALLED
	if _HOOKS_INSTALLED:
		return

	import frappe.utils.pdf as frappe_pdf

	orig_from_string = frappe_pdf.pdfkit.from_string
	if not getattr(orig_from_string, "_cskh_pdf_wrapped", False):

		def _wrapped_from_string(input_html, *args, **kwargs):
			rewritten_html = (
				inline_local_pdf_assets(input_html) if isinstance(input_html, str) else input_html
			)
			opts = kwargs.get("options")
			if opts is None and len(args) >= 2 and isinstance(args[1], dict):
				opts = args[1]
			if isinstance(opts, dict):
				for hf_key in ("header-html", "footer-html"):
					hf_path = opts.get(hf_key)
					if hf_path and isinstance(hf_path, str) and os.path.isfile(hf_path):
						hf_raw = Path(hf_path).read_text(encoding="utf-8", errors="replace")
						hf_inlined = inline_local_pdf_assets(hf_raw)
						if hf_inlined != hf_raw:
							Path(hf_path).write_text(hf_inlined, encoding="utf-8")
			return orig_from_string(rewritten_html, *args, **kwargs)

		_wrapped_from_string._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
		frappe_pdf.pdfkit.from_string = _wrapped_from_string

	try:
		import frappe.utils.weasyprint as frappe_weasyprint
		import weasyprint

		frappe_weasyprint.PrintFormatGenerator.get_element = staticmethod(_cskh_get_element)
		orig_import_wp = frappe_weasyprint.import_weasyprint
		if not getattr(orig_import_wp, "_cskh_pdf_wrapped", False):
			safe_fetcher = _make_weasyprint_url_fetcher(weasyprint.default_url_fetcher)

			def _wrapped_import_weasyprint():
				html_cls, css_cls = orig_import_wp()

				def _html_factory(*args, **kwargs):
					kwargs.setdefault("url_fetcher", safe_fetcher)
					return html_cls(*args, **kwargs)

				return _html_factory, css_cls

			_wrapped_import_weasyprint._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
			frappe_weasyprint.import_weasyprint = _wrapped_import_weasyprint
	except Exception:
		pass

	_HOOKS_INSTALLED = True


def on_print_pdf(*_args, **_kwargs) -> None:
	"""Frappe `on_print_pdf` hook ensuring CLI / `bench execute` callers also activate adapters."""
	ensure_pdf_runtime_hooks()
