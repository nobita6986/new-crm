"""Hermetic local-asset resolution and SSRF/file/RBAC lockdown for Frappe v15 PDF engines.

In the hardened P4 Compose topology (`compose.cskh-common.yaml`), `backend` runs on
`app_net` + `data_net` (`internal: true`) and background RQ workers (`queue-short`,
`queue-long`) run exclusively on `data_net` (`internal: true`), without direct network
routes to external TLS ingress (`proxy` on `edge_net`).

Upstream Frappe `v15.126.0` (`53bae3ef2550ca96320f036f581446783cd11b72`):
  - `frappe.utils.pdf` (`wkhtmltopdf 0.12.6.1-3`) rewrites `/assets/...` and `/files/...`
    via `scrub_urls(html)` to `http(s)://<site_host>/...`, writes the user's `sid` into a
    temporary `--cookie-jar`, and inlines `/private/files/...` via `find_file_by_url`
    (which bypasses `hooks.py` `has_permission["File"]` on reassigned `CRM Lead` records).
  - `frappe.utils.weasyprint` (`weasyprint==70.0`) passes `base_url = frappe.utils.get_url()`
    to `weasyprint.HTML(...)` / `weasyprint.CSS(...)` and has a single-branch recursion bug in
    `PrintFormatGenerator.get_element` when `@page` margin boxes precede `<html>`.

Security & functional invariants enforced by this module (`F-TRIAL-PDFSEC-01`, `F-TRIAL-HOOK-RB-01`):
  1. Identical safe local-only resource resolution across `wkhtmltopdf` and `WeasyPrint`
     (`HTML` and `CSS`), blocking external URLs, internal service URLs, HTTP redirects,
     protocol-relative URLs (`//...`, `\\\\...`), local `file://` / `filename=` / `file_obj=`
     reads, path traversal (`..`, `%2e%2e`), and symlinks (`O_NOFOLLOW`).
  2. Full `File` RBAC on `/private/files/...` (`Guest` denied, `frappe.has_permission("File", "read", doc=...)`
     enforcing `crm_cskh.permissions.linked_lead_has_permission` + parent document scope, and
     symlink-free `O_NOFOLLOW` disk reads).
  3. Thread-safe idempotent hook initialization (`threading.RLock`) across HTTP (`before_request`),
     RQ workers (`before_job`), CLI print (`on_print_pdf`), and test runners (`before_tests`).
"""

from __future__ import annotations

import base64
import inspect
import io
import mimetypes
import os
import re
import socket
import stat
import tempfile
import threading
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import frappe
from bs4 import BeautifulSoup

ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

_HOOKS_LOCK = threading.RLock()
_HOOKS_INSTALLED = False
_WRAP_INSTALL_COUNT = 0

_ORIG_PDFKIT_FROM_STRING: Any = None
_ORIG_PDFKIT_FROM_URL: Any = None
_ORIG_PDFKIT_FROM_FILE: Any = None
_ORIG_GET_BASE64_IMAGE: Any = None
_ORIG_INLINE_PRIVATE_IMAGES: Any = None
_ORIG_GET_COOKIE_OPTIONS: Any = None
_ORIG_IMPORT_WEASYPRINT: Any = None
_ORIG_WP_HTML: Any = None
_ORIG_WP_CSS: Any = None

_MAX_ASSET_BYTES = 25 * 1024 * 1024
_MAX_CSS_IMPORT_DEPTH = 3

_SAFE_TRANSPARENT_GIF_BYTES = base64.b64decode("R0lGODlhAQABAAAAACwAAAAAAQABAAA=")
_SAFE_TRANSPARENT_GIF_DATA_URI = "data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="

_EXTRA_MIME_TYPES: dict[str, str] = {
	".bmp": "image/bmp",
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

_SAFE_IMAGE_DATA_MIMES: frozenset[str] = frozenset(
	{
		"image/bmp",
		"image/gif",
		"image/jpeg",
		"image/jpg",
		"image/png",
		"image/svg+xml",
		"image/vnd.microsoft.icon",
		"image/webp",
		"image/x-icon",
	}
)

_SAFE_FONT_OR_BINARY_DATA_MIMES: frozenset[str] = frozenset(
	{
		"application/font-sfnt",
		"application/font-ttf",
		"application/font-woff",
		"application/font-woff2",
		"application/octet-stream",
		"application/vnd.ms-fontobject",
		"application/x-font-opentype",
		"application/x-font-truetype",
		"application/x-font-ttf",
		"application/x-font-woff",
		"font/otf",
		"font/sfnt",
		"font/ttf",
		"font/woff",
		"font/woff2",
	}
)

_FORBIDDEN_HTML_TAGS: tuple[str, ...] = (
	"applet",
	"audio",
	"base",
	"embed",
	"frame",
	"frameset",
	"iframe",
	"object",
	"portal",
	"script",
	"source",
	"track",
	"video",
)

_FORBIDDEN_WKHTMLTOPDF_OPTIONS: tuple[str, ...] = (
	"allow",
	"bypass-proxy-for",
	"cookie",
	"cookie-jar",
	"custom-header",
	"post",
	"post-file",
	"run-script",
	"user-style-sheet",
)

_CSS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_CSS_IMPORT_STMT_RE = re.compile(
	r"""@import\s+(?:url\(\s*(?:'([^']*)'|"([^"]*)"|([^'")\s]+))\s*\)|'([^']*)'|"([^"]*)")([^;{]*)(?:;|$)""",
	re.IGNORECASE,
)
_CSS_ANY_IMPORT_AT_RE = re.compile(r"@(?:\\[0-9a-fA-F]{1,6}\s?|\\.|[iI][mM][pP][oO][rR][tT])+", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"""url\(\s*(?:'([^']*)'|"([^"]*)"|([^'")\s]+))\s*\)""", re.IGNORECASE)
_SVG_UNSAFE_DECL_RE = re.compile(rb"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_SAFE_FRAGMENT_RE = re.compile(r"^#[A-Za-z0-9_.:-]+$")
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def _guess_mime(path_or_name: str) -> str:
	ext = Path(path_or_name).suffix.lower()
	if ext in _EXTRA_MIME_TYPES:
		return _EXTRA_MIME_TYPES[ext]
	guessed, _ = mimetypes.guess_type(path_or_name)
	return guessed or "application/octet-stream"


def _has_control_or_backslash(value: str) -> bool:
	if "\\" in value:
		return True
	return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _allowed_same_site_host_ports() -> tuple[set[str], set[int | None]]:
	"""Return strictly active site hostnames and allowed ports (never foreign or arbitrary ports)."""
	hosts: set[str] = set()
	ports: set[int | None] = {None, 80, 443}

	site = getattr(frappe.local, "site", None)
	if site:
		raw_site = str(site).strip().lower()
		if ":" in raw_site:
			h_part, p_part = raw_site.split(":", 1)
			if h_part:
				hosts.add(h_part)
			if p_part.isdigit():
				ports.add(int(p_part))
		elif raw_site:
			hosts.add(raw_site)

	req = getattr(frappe.local, "request", None)
	if req is not None:
		req_host = str(getattr(req, "host", "") or "").strip().lower()
		if ":" in req_host:
			h_part, p_part = req_host.split(":", 1)
			if h_part:
				hosts.add(h_part)
			if p_part.isdigit():
				ports.add(int(p_part))
		elif req_host:
			hosts.add(req_host)

	try:
		parsed_site_url = urlparse(frappe.utils.get_url())
		if parsed_site_url.hostname:
			hosts.add(parsed_site_url.hostname.lower())
		if parsed_site_url.port is not None:
			ports.add(parsed_site_url.port)
	except Exception:
		pass

	return {h for h in hosts if h}, ports


def _safe_weasyprint_base_url(raw_base_url: Any) -> str:
	"""Return a safe `http(s)://<same_site>/` base_url (never filesystem/`file://` or foreign host)."""
	allowed_hosts, allowed_ports = _allowed_same_site_host_ports()
	if isinstance(raw_base_url, str):
		cand = raw_base_url.strip()
		if cand and not cand.startswith("//") and not _has_control_or_backslash(cand):
			try:
				parsed = urlparse(cand)
				scheme = parsed.scheme.lower()
				host = (parsed.hostname or "").lower()
				if (
					scheme in ("http", "https")
					and not parsed.username
					and not parsed.password
					and host in allowed_hosts
					and parsed.port in allowed_ports
				):
					port_sfx = f":{parsed.port}" if parsed.port not in (None, 80, 443) else ""
					return f"{scheme}://{host}{port_sfx}/"
			except Exception:
				pass

	site = str(getattr(frappe.local, "site", None) or "localhost").strip().lower().split(":", 1)[0]
	safe_host = site if site and not _has_control_or_backslash(site) else "localhost"
	return f"http://{safe_host}/"


def _decode_and_split_url_path(raw_path: str) -> tuple[str, list[str]] | None:
	"""Validate and split a URL path while rejecting traversal, double-encoding, and backslashes."""
	if not raw_path or not raw_path.startswith("/") or raw_path.startswith("//"):
		return None
	if _has_control_or_backslash(raw_path):
		return None

	decoded = unquote(raw_path)
	if not decoded.startswith("/") or decoded.startswith("//") or decoded.endswith("/"):
		return None
	if _has_control_or_backslash(decoded):
		return None

	# Reject double-encoded path separators or dot-segments (`%252e`, `%252f`, `%255c`)
	decoded_twice = unquote(decoded)
	if decoded_twice != decoded and any(ch in decoded_twice for ch in (".", "/", "\\", "\x00")):
		return None

	segments = decoded.split("/")[1:]
	if not segments or any(seg in ("", ".", "..") for seg in segments):
		return None
	return decoded, segments


def _open_regular_file_nofollow(path: Path, *, max_bytes: int = _MAX_ASSET_BYTES) -> bytes | None:
	flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
	try:
		st_link = os.lstat(path)
		if stat.S_ISLNK(st_link.st_mode) or not stat.S_ISREG(st_link.st_mode):
			return None
		fd = os.open(str(path), flags)
	except OSError:
		return None

	try:
		with os.fdopen(fd, "rb") as fh:
			st_fd = os.fstat(fh.fileno())
			if not stat.S_ISREG(st_fd.st_mode) or st_fd.st_size < 0 or st_fd.st_size > max_bytes:
				return None
			data = fh.read(max_bytes + 1)
			if len(data) > max_bytes:
				return None
			return data
	except OSError:
		return None


def _read_regular_file_no_symlinks(
	root_dir: Path,
	rel_parts: list[str],
	*,
	site_root: Path | None = None,
	max_bytes: int = _MAX_ASSET_BYTES,
) -> tuple[bytes, Path] | None:
	"""Walk `root_dir / *rel_parts` rejecting any symlink segment and read with `O_NOFOLLOW`."""
	try:
		if not os.path.lexists(root_dir) or stat.S_ISLNK(os.lstat(root_dir).st_mode):
			return None
		real_root = root_dir.resolve()
		if not real_root.is_dir():
			return None
		if site_root is not None and not real_root.is_relative_to(site_root):
			return None

		cur = real_root
		for seg in rel_parts:
			if not seg or seg in (".", "..") or "/" in seg or "\\" in seg:
				return None
			cur = cur / seg
			if not os.path.lexists(cur):
				return None
			st_seg = os.lstat(cur)
			if stat.S_ISLNK(st_seg.st_mode):
				return None

		resolved = cur.resolve()
		if not resolved.is_relative_to(real_root):
			return None
	except OSError:
		return None

	raw_bytes = _open_regular_file_nofollow(cur, max_bytes=max_bytes)
	if raw_bytes is None:
		return None
	return raw_bytes, resolved


def _approved_asset_roots(sites_dir: Path) -> tuple[Path | None, list[Path], Path | None]:
	assets_link = sites_dir / "assets"
	if not assets_link.exists():
		return None, [], None
	try:
		assets_root = assets_link.resolve()
	except OSError:
		return None, [], None
	if not assets_root.is_dir():
		return None, [], None

	approved: list[Path] = [assets_root]
	for cand_bench in (assets_root.parent, sites_dir.resolve().parent):
		apps_dir = cand_bench / "apps"
		if apps_dir.is_dir():
			try:
				for app_repo in apps_dir.iterdir():
					if not app_repo.is_dir():
						continue
					for pkg_dir in app_repo.iterdir():
						pub_dir = pkg_dir / "public"
						if pub_dir.is_dir():
							pub_real = pub_dir.resolve()
							if pub_real not in approved:
								approved.append(pub_real)
			except OSError:
				pass

	site_root: Path | None = None
	if getattr(frappe.local, "site", None):
		try:
			site_root = Path(frappe.get_site_path()).resolve()
		except Exception:
			site_root = None

	return assets_root, approved, site_root


def _read_asset_file_jailed(sites_dir: Path, rel_parts: list[str]) -> tuple[bytes, Path] | None:
	assets_root, approved_roots, site_root = _approved_asset_roots(sites_dir)
	if assets_root is None:
		return None
	try:
		cand = assets_root.joinpath(*rel_parts)
		resolved = cand.resolve()
	except OSError:
		return None

	if not any(resolved.is_relative_to(root) for root in approved_roots):
		return None
	if (
		site_root is not None
		and resolved.is_relative_to(site_root)
		and not assets_root.is_relative_to(site_root)
	):
		return None

	raw_bytes = _open_regular_file_nofollow(resolved)
	if raw_bytes is None:
		return None
	return raw_bytes, resolved


def _is_user_authorized_for_private_file(file_doc: Any, user: str) -> bool:
	if not user or user == "Guest":
		return False
	if int(getattr(file_doc, "is_private", 0) or 0) != 1:
		return False
	if not frappe.has_permission("File", "read", doc=file_doc, user=user):
		return False
	att_dt = getattr(file_doc, "attached_to_doctype", None)
	att_dn = getattr(file_doc, "attached_to_name", None)
	if att_dt and att_dn and att_dt != "CSKH Export Job":
		if not frappe.has_permission(att_dt, "read", doc=att_dn, user=user):
			return False
	return True


def _resolve_private_file_resource(
	url_path: str,
	rel_parts: list[str],
	query_str: str,
) -> tuple[bytes, str, Path | None] | None:
	"""Resolve `/private/files/...` enforcing full `File` RBAC + symlink-free `O_NOFOLLOW` disk read."""
	if not getattr(frappe.local, "site", None):
		return None
	session_obj = getattr(frappe, "session", None)
	user = str(getattr(session_obj, "user", "") or "").strip()
	if not user or user == "Guest":
		return None

	try:
		site_root = Path(frappe.get_site_path()).resolve()
		private_files_dir = Path(frappe.get_site_path("private", "files"))
	except Exception:
		return None

	qs = parse_qs(query_str or "")
	fid = (qs.get("fid") or [None])[0]
	filters: dict[str, Any] = {"file_url": url_path, "is_private": 1}
	if fid:
		filters["name"] = str(fid)

	try:
		file_names = frappe.get_all("File", filters=filters, pluck="name")
	except Exception:
		return None
	if not file_names:
		return None

	authorized = False
	for fname in file_names:
		try:
			file_doc = frappe.get_doc("File", fname)
			if str(getattr(file_doc, "file_url", "")) != url_path:
				continue
			if _is_user_authorized_for_private_file(file_doc, user):
				authorized = True
				break
		except Exception:
			continue

	if not authorized:
		return None

	disk_hit = _read_regular_file_no_symlinks(private_files_dir, rel_parts, site_root=site_root)
	if disk_hit is None:
		return None
	raw_bytes, resolved_path = disk_hit
	return raw_bytes, _guess_mime(resolved_path.name), resolved_path


def _resolve_same_site_resource(
	raw_url: str,
	*,
	include_private_files: bool = False,
) -> tuple[bytes, str, Path | None] | None:
	"""Resolve a same-site `/assets/...`, `/files/...`, or `/private/files/...` resource safely."""
	cleaned = (raw_url or "").strip()
	if not cleaned or _has_control_or_backslash(cleaned):
		return None
	if cleaned.startswith("//"):
		return None
	low = cleaned.lower()
	if low.startswith(
		("data:", "mailto:", "tel:", "javascript:", "vbscript:", "file:", "ftp:", "blob:", "#", "about:")
	):
		return None

	try:
		parsed = urlparse(cleaned)
	except ValueError:
		return None

	if parsed.username or parsed.password:
		return None
	if not parsed.scheme and parsed.netloc:
		# Protocol-relative URL (`//host/...`) is strictly forbidden
		return None
	if parsed.scheme:
		if parsed.scheme.lower() not in ("http", "https"):
			return None
		host = (parsed.hostname or "").lower()
		allowed_hosts, allowed_ports = _allowed_same_site_host_ports()
		if not host or host not in allowed_hosts or parsed.port not in allowed_ports:
			return None

	decoded_res = _decode_and_split_url_path(parsed.path or "")
	if decoded_res is None:
		return None
	url_path, segments = decoded_res

	raw_sites = getattr(frappe.local, "sites_path", None) or "."
	sites_dir = Path(raw_sites)

	if segments[0] == "assets" and len(segments) >= 2:
		hit = _read_asset_file_jailed(sites_dir, segments[1:])
		if hit is None:
			return None
		raw_bytes, resolved = hit
		return raw_bytes, _guess_mime(resolved.name), resolved

	if segments[0] == "files" and len(segments) >= 2 and getattr(frappe.local, "site", None):
		try:
			site_root = Path(frappe.get_site_path()).resolve()
			public_files_dir = Path(frappe.get_site_path("public", "files"))
		except Exception:
			return None
		hit = _read_regular_file_no_symlinks(public_files_dir, segments[1:], site_root=site_root)
		if hit is None:
			return None
		raw_bytes, resolved = hit
		return raw_bytes, _guess_mime(resolved.name), resolved

	if include_private_files and segments[:2] == ["private", "files"] and len(segments) >= 3:
		return _resolve_private_file_resource(url_path, segments[2:], parsed.query or "")

	return None


def _local_tag_name(tag: str) -> str:
	return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _sanitize_svg_bytes(svg_bytes: bytes) -> bytes | None:
	"""Sanitize SVG XML against XXE, `<script>`, `<foreignObject>`, and external/unauthorized refs."""
	if not svg_bytes or len(svg_bytes) > _MAX_ASSET_BYTES:
		return None
	if _SVG_UNSAFE_DECL_RE.search(svg_bytes[:8192]):
		return None
	try:
		root = ET.fromstring(svg_bytes)
	except ET.ParseError:
		return None

	if _local_tag_name(root.tag) != "svg":
		return None

	def _walk(parent: ET.Element) -> None:
		for child in list(parent):
			lname = _local_tag_name(child.tag)
			if lname in ("script", "foreignobject", "iframe", "embed", "object"):
				parent.remove(child)
				continue
			if lname == "style":
				child.text = sanitize_pdf_css(child.text or "", include_private_files=False)
			for attr_key in list(child.attrib):
				attr_local = _local_tag_name(attr_key)
				attr_val = str(child.attrib.get(attr_key) or "").strip()
				if attr_local.startswith("on"):
					del child.attrib[attr_key]
				elif attr_local == "style":
					child.attrib[attr_key] = sanitize_pdf_css(attr_val, include_private_files=False)
				elif attr_key in ("href", _XLINK_HREF) or attr_local in ("href", "src"):
					if _SAFE_FRAGMENT_RE.match(attr_val):
						continue
					safe_data = _sanitize_data_uri(attr_val, allow_fonts=False)
					if safe_data is not None:
						child.attrib[attr_key] = safe_data
						continue
					resolved = _resolve_same_site_resource(attr_val, include_private_files=False)
					if resolved is not None:
						img_b, img_m, _ = resolved
						encoded = _encode_safe_image_data_uri(img_b, img_m)
						if encoded is not None:
							child.attrib[attr_key] = encoded
							continue
					del child.attrib[attr_key]
			_walk(child)

	for top_attr in list(root.attrib):
		top_local = _local_tag_name(top_attr)
		if top_local.startswith("on"):
			del root.attrib[top_attr]
		elif top_local == "style":
			root.attrib[top_attr] = sanitize_pdf_css(
				str(root.attrib.get(top_attr) or ""), include_private_files=False
			)

	_walk(root)
	return ET.tostring(root, encoding="utf-8")


def _encode_safe_image_data_uri(raw_bytes: bytes, mime: str) -> str | None:
	norm_mime = mime.split(";", 1)[0].strip().lower()
	if norm_mime not in _SAFE_IMAGE_DATA_MIMES:
		return None
	if norm_mime == "image/svg+xml":
		sanitized = _sanitize_svg_bytes(raw_bytes)
		if sanitized is None:
			return None
		raw_bytes = sanitized
	b64 = base64.b64encode(raw_bytes).decode("ascii")
	return f"data:{norm_mime};base64,{b64}"


def _decode_data_uri_bytes(raw_uri: str) -> tuple[str, bytes] | None:
	if not raw_uri.lower().startswith("data:") or "," not in raw_uri:
		return None
	header, payload = raw_uri[5:].split(",", 1)
	parts = [p.strip() for p in header.split(";") if p.strip()]
	is_b64 = any(p.lower() == "base64" for p in parts)
	mime = "text/plain"
	if parts and "/" in parts[0]:
		mime = parts[0].lower()
	try:
		if is_b64:
			raw_bytes = base64.b64decode(payload, validate=False)
		else:
			raw_bytes = unquote(payload).encode("utf-8")
	except Exception:
		return None
	if len(raw_bytes) > _MAX_ASSET_BYTES:
		return None
	return mime, raw_bytes


def _sanitize_data_uri(raw_uri: str, *, allow_fonts: bool = False) -> str | None:
	cleaned = (raw_uri or "").strip()
	if cleaned == "data:,":
		return "data:," if allow_fonts else None
	decoded = _decode_data_uri_bytes(cleaned)
	if decoded is None:
		return None
	mime, raw_bytes = decoded
	if mime in _SAFE_IMAGE_DATA_MIMES:
		return _encode_safe_image_data_uri(raw_bytes, mime)
	if allow_fonts and mime in _SAFE_FONT_OR_BINARY_DATA_MIMES:
		b64 = base64.b64encode(raw_bytes).decode("ascii")
		return f"data:{mime};base64,{b64}"
	return None


def _resolve_css_relative_asset(raw_ref: str, css_file: Path | None) -> tuple[bytes, str, Path] | None:
	if css_file is None or not raw_ref or _has_control_or_backslash(raw_ref):
		return None
	if raw_ref.startswith(("/", "#", "//")) or ":" in raw_ref:
		return None
	try:
		parsed_ref = urlparse(raw_ref)
	except ValueError:
		return None
	if parsed_ref.scheme or parsed_ref.netloc:
		return None
	rel_part = unquote(parsed_ref.path or "")
	if not rel_part or _has_control_or_backslash(rel_part):
		return None
	if unquote(rel_part) != rel_part:
		return None
	raw_sites = getattr(frappe.local, "sites_path", None) or "."
	assets_root, approved_roots, site_root = _approved_asset_roots(Path(raw_sites))
	if assets_root is None:
		return None
	try:
		cand = (css_file.parent / rel_part).resolve()
	except OSError:
		return None
	if not any(cand.is_relative_to(r) for r in approved_roots):
		return None
	if site_root is not None and cand.is_relative_to(site_root) and not assets_root.is_relative_to(site_root):
		return None
	raw_bytes = _open_regular_file_nofollow(cand)
	if raw_bytes is None:
		return None
	return raw_bytes, _guess_mime(cand.name), cand


def sanitize_pdf_css(
	css_text: str,
	*,
	css_file: Path | None = None,
	include_private_files: bool = True,
	_depth: int = 0,
) -> str:
	"""Sanitize CSS for both `wkhtmltopdf` and `WeasyPrint`, locking `@import` and `url(...)`."""
	if not css_text:
		return css_text

	without_comments = _CSS_COMMENT_RE.sub("", css_text)

	def _replace_import(match: re.Match[str]) -> str:
		if _depth >= _MAX_CSS_IMPORT_DEPTH:
			return ""
		raw_ref = next((g for g in match.groups()[:5] if g is not None), "").strip()
		media_clause = (match.group(6) or "").strip()
		if not raw_ref or _has_control_or_backslash(raw_ref):
			return ""
		resolved = _resolve_same_site_resource(raw_ref, include_private_files=False)
		if resolved is None:
			resolved = _resolve_css_relative_asset(raw_ref, css_file)
		if resolved is None:
			return ""
		imp_bytes, imp_mime, imp_path = resolved
		if imp_mime != "text/css":
			return ""
		inlined = sanitize_pdf_css(
			imp_bytes.decode("utf-8", errors="replace"),
			css_file=imp_path,
			include_private_files=False,
			_depth=_depth + 1,
		)
		if media_clause and not any(ch in media_clause for ch in ("{", "}", ";", "@", "\\")):
			return f"@media {media_clause} {{\n{inlined}\n}}"
		return inlined

	after_imports = _CSS_IMPORT_STMT_RE.sub(_replace_import, without_comments)
	if _CSS_ANY_IMPORT_AT_RE.search(after_imports):
		# Strip any malformed or backslash-escaped residual @import token
		after_imports = re.sub(
			r"@(?:\\[0-9a-fA-F]{1,6}\s?|\\.|[iI][mM][pP][oO][rR][tT])+[^;{]*(?:;|\{[^}]*\}|$)",
			"",
			after_imports,
		)

	def _replace_url(match: re.Match[str]) -> str:
		raw_ref = next((g for g in match.groups() if g is not None), "").strip()
		if not raw_ref:
			return 'url("data:,")'
		if _SAFE_FRAGMENT_RE.match(raw_ref):
			return f'url("{raw_ref}")'
		if raw_ref.lower().startswith("data:"):
			safe_data = _sanitize_data_uri(raw_ref, allow_fonts=True)
			return f'url("{safe_data}")' if safe_data is not None else 'url("data:,")'
		if _has_control_or_backslash(raw_ref):
			return 'url("data:,")'

		resolved = _resolve_same_site_resource(raw_ref, include_private_files=include_private_files)
		if resolved is None:
			resolved = _resolve_css_relative_asset(raw_ref, css_file)

		if resolved is not None:
			raw_bytes, mime, _ = resolved
			if mime in _SAFE_IMAGE_DATA_MIMES:
				safe_img = _encode_safe_image_data_uri(raw_bytes, mime)
				return f'url("{safe_img}")' if safe_img is not None else 'url("data:,")'
			if mime in _SAFE_FONT_OR_BINARY_DATA_MIMES:
				b64 = base64.b64encode(raw_bytes).decode("ascii")
				return f'url("data:{mime};base64,{b64}")'

		return 'url("data:,")'

	return _CSS_URL_RE.sub(_replace_url, after_imports)


def _resolve_image_attr_to_safe_data_uri(raw_src: str, *, include_private_files: bool = True) -> str:
	cleaned = (raw_src or "").strip()
	if not cleaned:
		return _SAFE_TRANSPARENT_GIF_DATA_URI
	if cleaned.lower().startswith("data:"):
		safe_data = _sanitize_data_uri(cleaned, allow_fonts=False)
		return safe_data if safe_data is not None else _SAFE_TRANSPARENT_GIF_DATA_URI

	resolved = _resolve_same_site_resource(cleaned, include_private_files=include_private_files)
	if resolved is not None:
		img_bytes, mime, _ = resolved
		safe_img = _encode_safe_image_data_uri(img_bytes, mime)
		if safe_img is not None:
			return safe_img
	return _SAFE_TRANSPARENT_GIF_DATA_URI


def inline_local_pdf_assets(html: str) -> str:
	"""Sanitize HTML & CSS and inline authorized local `/assets/...`, `/files/...`, and `/private/files/...`."""
	if not html:
		return html

	soup = BeautifulSoup(html, "html.parser")

	for bad_tag in list(soup.find_all(_FORBIDDEN_HTML_TAGS)):
		bad_tag.decompose()

	for meta_tag in list(soup.find_all("meta")):
		if meta_tag.has_attr("http-equiv"):
			meta_tag.decompose()

	for link_tag in list(soup.find_all("link")):
		rels = link_tag.get("rel") or []
		if isinstance(rels, str):
			rels = [rels]
		is_stylesheet = any(str(r).lower() == "stylesheet" for r in rels)
		href = str(link_tag.get("href") or "").strip()
		if is_stylesheet and href:
			resolved = _resolve_same_site_resource(href, include_private_files=False)
			if resolved is not None and resolved[1] == "text/css":
				css_bytes, _, css_path = resolved
				css_text = css_bytes.decode("utf-8", errors="replace")
				inlined_css = sanitize_pdf_css(css_text, css_file=css_path, include_private_files=False)
				style_tag = soup.new_tag("style")
				style_tag["type"] = "text/css"
				media_val = str(link_tag.get("media") or "").strip()
				if media_val:
					style_tag["media"] = media_val
				style_tag.string = inlined_css
				link_tag.replace_with(style_tag)
				continue
		# Decompose all non-stylesheet or unresolved/external/internal <link> elements
		link_tag.decompose()

	for style_tag in soup.find_all("style"):
		raw_css = style_tag.get_text()
		if raw_css:
			style_tag.string = sanitize_pdf_css(raw_css, include_private_files=True)

	for tag in soup.find_all(True):
		tname = str(tag.name or "").lower()
		for attr_name in list(tag.attrs):
			alow = str(attr_name).lower()
			if alow.startswith("on") or alow in (
				"srcset",
				"imagesrcset",
				"ping",
				"dynsrc",
				"lowsrc",
				"formaction",
			):
				del tag.attrs[attr_name]
			elif alow == "style":
				tag.attrs[attr_name] = sanitize_pdf_css(
					str(tag.attrs[attr_name] or ""), include_private_files=True
				)
			elif alow in ("background", "poster"):
				tag.attrs[attr_name] = _resolve_image_attr_to_safe_data_uri(
					str(tag.attrs[attr_name] or ""),
					include_private_files=True,
				)

		if tname == "img" or (tname == "input" and str(tag.get("type") or "").lower() == "image"):
			raw_src = str(tag.get("src") or "").strip()
			tag["src"] = _resolve_image_attr_to_safe_data_uri(raw_src, include_private_files=True)
		elif tname in ("image", "feimage", "use"):
			for href_attr in ("href", "xlink:href"):
				if tag.has_attr(href_attr):
					hval = str(tag.get(href_attr) or "").strip()
					if _SAFE_FRAGMENT_RE.match(hval):
						continue
					tag[href_attr] = _resolve_image_attr_to_safe_data_uri(hval, include_private_files=True)
		elif tname in ("a", "area") and tag.has_attr("href"):
			hval = str(tag.get("href") or "").strip()
			hlow = hval.lower()
			if (
				_has_control_or_backslash(hval)
				or hval.startswith("//")
				or hlow.startswith(("javascript:", "vbscript:", "data:", "file:"))
			):
				tag["href"] = "#"

	return str(soup)


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


def _make_weasyprint_url_fetcher(weasyprint_mod):
	"""Build a local-only URL fetcher compatible with WeasyPrint 70+ (`URLFetcher`) and older APIs."""
	try:
		from weasyprint.urls import URLFetcher, URLFetcherResponse

		class _LocalSafeWeasyPrintURLFetcher(URLFetcher):
			def __init__(self):
				init_kwargs: dict[str, Any] = {"fail_on_errors": False}
				try:
					sig_params = inspect.signature(URLFetcher.__init__).parameters
					if "allow_redirects" in sig_params:
						init_kwargs["allow_redirects"] = False
					if "allowed_protocols" in sig_params:
						init_kwargs["allowed_protocols"] = {"data"}
				except (TypeError, ValueError):
					pass
				super().__init__(**init_kwargs)

			def fetch(self, url, headers=None):
				cleaned = (url or "").strip()
				if cleaned.lower().startswith("data:"):
					safe_data = _sanitize_data_uri(cleaned, allow_fonts=True)
					if safe_data is None:
						raise ValueError(f"Blocked unsafe data: URI in WeasyPrint: {cleaned[:80]}")
					decoded = _decode_data_uri_bytes(safe_data)
					if decoded is None:
						return super().fetch(safe_data, headers=headers)
					mime, raw_bytes = decoded
					return URLFetcherResponse(
						safe_data,
						body=raw_bytes,
						headers={"Content-Type": mime},
						status=200,
					)
				resolved = _resolve_same_site_resource(cleaned, include_private_files=True)
				if resolved is not None:
					raw_bytes, mime, css_or_asset_path = resolved
					if mime == "text/css":
						sanitized_css = sanitize_pdf_css(
							raw_bytes.decode("utf-8", errors="replace"),
							css_file=css_or_asset_path,
							include_private_files=False,
						)
						raw_bytes = sanitized_css.encode("utf-8")
					elif mime == "image/svg+xml":
						sanitized_svg = _sanitize_svg_bytes(raw_bytes)
						if sanitized_svg is None:
							raise ValueError(f"Blocked unsafe SVG resource in WeasyPrint: {cleaned}")
						raw_bytes = sanitized_svg
					return URLFetcherResponse(
						cleaned,
						body=raw_bytes,
						headers={"Content-Type": mime},
						status=200,
					)
				raise ValueError(
					f"Blocked non-local, unauthorized, or redirect PDF resource URL in isolated runtime: {cleaned}"
				)

		return _LocalSafeWeasyPrintURLFetcher()
	except ImportError:
		default_fetcher = getattr(weasyprint_mod, "default_url_fetcher", None)
		if default_fetcher is None:
			return None

		def _legacy_fetcher(url: str, timeout: int = 10, ssl_context=None):
			cleaned = (url or "").strip()
			if cleaned.lower().startswith("data:"):
				safe_data = _sanitize_data_uri(cleaned, allow_fonts=True)
				if safe_data is None:
					raise ValueError(f"Blocked unsafe data: URI in WeasyPrint: {cleaned[:80]}")
				return default_fetcher(safe_data, timeout=timeout, ssl_context=ssl_context)
			resolved = _resolve_same_site_resource(cleaned, include_private_files=True)
			if resolved is not None:
				raw_bytes, mime, css_or_asset_path = resolved
				if mime == "text/css":
					raw_bytes = sanitize_pdf_css(
						raw_bytes.decode("utf-8", errors="replace"),
						css_file=css_or_asset_path,
						include_private_files=False,
					).encode("utf-8")
				elif mime == "image/svg+xml":
					sanitized_svg = _sanitize_svg_bytes(raw_bytes)
					if sanitized_svg is None:
						raise ValueError(f"Blocked unsafe SVG resource in WeasyPrint: {cleaned}")
					raw_bytes = sanitized_svg
				return {"string": raw_bytes, "mime_type": mime}
			raise ValueError(f"Blocked non-local or unauthorized PDF resource URL: {cleaned}")

		return _legacy_fetcher


def _build_safe_weasyprint_factories(orig_html_cls, orig_css_cls, safe_fetcher):
	"""Wrap both WeasyPrint `HTML` and `CSS` classes with identical safe-fetch & file-block rules."""

	def _safe_html_factory(*args, **kwargs):
		if args or any(kwargs.get(k) is not None for k in ("guess", "filename", "file_obj", "url")):
			raise ValueError(
				"Direct filename/file_obj/url/guess input to WeasyPrint HTML is blocked by PDF security policy; pass string="
			)
		raw_html = kwargs.get("string")
		if not isinstance(raw_html, str):
			raise ValueError("WeasyPrint HTML requires string= str input under PDF security policy")
		call_kwargs = dict(kwargs)
		call_kwargs["string"] = inline_local_pdf_assets(raw_html)
		call_kwargs["base_url"] = _safe_weasyprint_base_url(call_kwargs.get("base_url"))
		if safe_fetcher is not None:
			call_kwargs["url_fetcher"] = safe_fetcher
		return orig_html_cls(**call_kwargs)

	def _safe_css_factory(*args, **kwargs):
		if args or any(kwargs.get(k) is not None for k in ("guess", "filename", "file_obj")):
			raise ValueError(
				"Direct filename/file_obj/guess input to WeasyPrint CSS is blocked by PDF security policy; pass string="
			)
		call_kwargs = dict(kwargs)
		if call_kwargs.get("url") is not None:
			raw_css_url = str(call_kwargs.pop("url"))
			resolved = _resolve_same_site_resource(raw_css_url, include_private_files=False)
			if resolved is None or resolved[1] != "text/css":
				raise ValueError(
					f"Blocked non-local or unauthorized stylesheet URL in WeasyPrint CSS: {raw_css_url}"
				)
			css_bytes, _, css_path = resolved
			call_kwargs["string"] = sanitize_pdf_css(
				css_bytes.decode("utf-8", errors="replace"),
				css_file=css_path,
				include_private_files=False,
			)
		else:
			raw_css = call_kwargs.get("string")
			if not isinstance(raw_css, str):
				raise ValueError("WeasyPrint CSS requires string= str input under PDF security policy")
			call_kwargs["string"] = sanitize_pdf_css(raw_css, include_private_files=True)

		call_kwargs["base_url"] = _safe_weasyprint_base_url(call_kwargs.get("base_url"))
		if safe_fetcher is not None:
			call_kwargs["url_fetcher"] = safe_fetcher
		return orig_css_cls(**call_kwargs)

	_safe_html_factory._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
	_safe_css_factory._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
	return _safe_html_factory, _safe_css_factory


def _is_safe_temp_header_footer_path(candidate_path: str) -> bool:
	try:
		p = Path(candidate_path)
		if not os.path.lexists(p) or stat.S_ISLNK(os.lstat(p).st_mode):
			return False
		resolved = p.resolve()
		tmp_roots = {Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
		if not any(resolved.is_relative_to(tr) for tr in tmp_roots):
			return False
		return resolved.is_file() and resolved.suffix.lower() == ".html"
	except OSError:
		return False


def _sanitize_wkhtmltopdf_options(opts: dict[str, Any]) -> dict[str, Any]:
	clean_opts = dict(opts)
	cjar = clean_opts.get("cookie-jar")
	if isinstance(cjar, str) and cjar and os.path.isfile(cjar) and not os.path.islink(cjar):
		try:
			if Path(cjar).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) or Path(
				cjar
			).resolve().is_relative_to(Path("/tmp").resolve()):
				os.remove(cjar)
		except OSError:
			pass

	for bad_key in _FORBIDDEN_WKHTMLTOPDF_OPTIONS:
		clean_opts.pop(bad_key, None)

	for hf_key in ("header-html", "footer-html"):
		hf_path = clean_opts.get(hf_key)
		if hf_path is None:
			continue
		if not isinstance(hf_path, str) or not _is_safe_temp_header_footer_path(hf_path):
			clean_opts.pop(hf_key, None)
			continue
		hf_raw_bytes = _open_regular_file_nofollow(Path(hf_path))
		if hf_raw_bytes is None:
			clean_opts.pop(hf_key, None)
			continue
		hf_raw = hf_raw_bytes.decode("utf-8", errors="replace")
		hf_sanitized = inline_local_pdf_assets(hf_raw)
		Path(hf_path).write_text(hf_sanitized, encoding="utf-8")

	clean_opts["disable-local-file-access"] = ""
	clean_opts["disable-javascript"] = ""
	clean_opts["load-error-handling"] = "ignore"
	clean_opts["load-media-error-handling"] = "ignore"
	clean_opts["proxy"] = "http://127.0.0.1:1"
	return clean_opts


def _guarded_frappe_get_base64_image(src: str) -> str | None:
	"""Strict replacement for `frappe.utils.pdf._get_base64_image` enforcing `linked_lead_has_permission`."""
	cleaned = (src or "").strip()
	if not cleaned or _has_control_or_backslash(cleaned) or cleaned.startswith("//"):
		return None
	try:
		parsed = urlparse(cleaned)
	except ValueError:
		return None
	decoded_res = _decode_and_split_url_path(parsed.path or "")
	if decoded_res is None:
		return None
	_, segments = decoded_res
	if segments[:2] != ["private", "files"]:
		return None
	resolved = _resolve_same_site_resource(cleaned, include_private_files=True)
	if resolved is None:
		return None
	raw_bytes, mime, _ = resolved
	return _encode_safe_image_data_uri(raw_bytes, mime)


def _guarded_frappe_inline_private_images(html: str) -> str:
	if not html:
		return html
	soup = BeautifulSoup(html, "html.parser")
	for img in soup.find_all("img"):
		raw_src = str(img.get("src") or "").strip()
		if not raw_src:
			continue
		b64_uri = _guarded_frappe_get_base64_image(raw_src)
		if b64_uri:
			img["src"] = b64_uri
	return str(soup)


def _guarded_frappe_get_cookie_options() -> dict[str, Any]:
	"""Never expose session `sid` cookies to external `wkhtmltopdf` subprocess CLI/files."""
	return {}


def _install_pdf_runtime_hooks_locked() -> None:
	global _WRAP_INSTALL_COUNT
	global _ORIG_PDFKIT_FROM_STRING, _ORIG_PDFKIT_FROM_URL, _ORIG_PDFKIT_FROM_FILE
	global _ORIG_GET_BASE64_IMAGE, _ORIG_INLINE_PRIVATE_IMAGES, _ORIG_GET_COOKIE_OPTIONS
	global _ORIG_IMPORT_WEASYPRINT, _ORIG_WP_HTML, _ORIG_WP_CSS

	import frappe.utils.pdf as frappe_pdf

	if not getattr(frappe_pdf.pdfkit.from_string, "_cskh_pdf_wrapped", False):
		if _ORIG_PDFKIT_FROM_STRING is None:
			_ORIG_PDFKIT_FROM_STRING = frappe_pdf.pdfkit.from_string

		def _wrapped_from_string(input_html, *args, **kwargs):
			rewritten_html = (
				inline_local_pdf_assets(input_html) if isinstance(input_html, str) else input_html
			)
			call_args = list(args)
			call_kwargs = dict(kwargs)
			if "options" in call_kwargs or len(call_args) < 2:
				raw_opts = call_kwargs.get("options")
				call_kwargs["options"] = _sanitize_wkhtmltopdf_options(
					raw_opts if isinstance(raw_opts, dict) else {}
				)
			elif isinstance(call_args[1], dict):
				call_args[1] = _sanitize_wkhtmltopdf_options(call_args[1])
			return _ORIG_PDFKIT_FROM_STRING(rewritten_html, *call_args, **call_kwargs)

		_wrapped_from_string._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
		frappe_pdf.pdfkit.from_string = _wrapped_from_string
		_WRAP_INSTALL_COUNT += 1

	for blocked_api_name, orig_slot_name in (
		("from_url", "_ORIG_PDFKIT_FROM_URL"),
		("from_file", "_ORIG_PDFKIT_FROM_FILE"),
	):
		cur_fn = getattr(frappe_pdf.pdfkit, blocked_api_name, None)
		if cur_fn is not None and not getattr(cur_fn, "_cskh_pdf_wrapped", False):
			if globals()[orig_slot_name] is None:
				globals()[orig_slot_name] = cur_fn

			def _blocked_pdfkit_call(*_a, _name=blocked_api_name, **_kw):
				raise PermissionError(
					f"pdfkit.{_name} is disabled by CSKH PDF security policy; render sanitized HTML strings only"
				)

			_blocked_pdfkit_call._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
			setattr(frappe_pdf.pdfkit, blocked_api_name, _blocked_pdfkit_call)

	if not getattr(frappe_pdf._get_base64_image, "_cskh_pdf_wrapped", False):
		if _ORIG_GET_BASE64_IMAGE is None:
			_ORIG_GET_BASE64_IMAGE = frappe_pdf._get_base64_image
		_guarded_frappe_get_base64_image._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
		frappe_pdf._get_base64_image = _guarded_frappe_get_base64_image

	if not getattr(frappe_pdf.inline_private_images, "_cskh_pdf_wrapped", False):
		if _ORIG_INLINE_PRIVATE_IMAGES is None:
			_ORIG_INLINE_PRIVATE_IMAGES = frappe_pdf.inline_private_images
		_guarded_frappe_inline_private_images._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
		frappe_pdf.inline_private_images = _guarded_frappe_inline_private_images

	if not getattr(frappe_pdf.get_cookie_options, "_cskh_pdf_wrapped", False):
		if _ORIG_GET_COOKIE_OPTIONS is None:
			_ORIG_GET_COOKIE_OPTIONS = frappe_pdf.get_cookie_options
		_guarded_frappe_get_cookie_options._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
		frappe_pdf.get_cookie_options = _guarded_frappe_get_cookie_options

	try:
		import frappe.utils.weasyprint as frappe_weasyprint
		import weasyprint
	except ImportError:
		frappe_weasyprint = None
		weasyprint = None

	if frappe_weasyprint is not None and weasyprint is not None:
		frappe_weasyprint.PrintFormatGenerator.get_element = staticmethod(_cskh_get_element)
		if _ORIG_WP_HTML is None and not getattr(weasyprint.HTML, "_cskh_pdf_wrapped", False):
			_ORIG_WP_HTML = weasyprint.HTML
		if _ORIG_WP_CSS is None and not getattr(weasyprint.CSS, "_cskh_pdf_wrapped", False):
			_ORIG_WP_CSS = weasyprint.CSS

		safe_fetcher = _make_weasyprint_url_fetcher(weasyprint)
		if _ORIG_WP_HTML is not None and _ORIG_WP_CSS is not None:
			safe_html_cls, safe_css_cls = _build_safe_weasyprint_factories(
				_ORIG_WP_HTML,
				_ORIG_WP_CSS,
				safe_fetcher,
			)
			if not getattr(weasyprint.HTML, "_cskh_pdf_wrapped", False):
				weasyprint.HTML = safe_html_cls
			if not getattr(weasyprint.CSS, "_cskh_pdf_wrapped", False):
				weasyprint.CSS = safe_css_cls

		if not getattr(frappe_weasyprint.import_weasyprint, "_cskh_pdf_wrapped", False):
			if _ORIG_IMPORT_WEASYPRINT is None:
				_ORIG_IMPORT_WEASYPRINT = frappe_weasyprint.import_weasyprint

			def _wrapped_import_weasyprint():
				raw_html_cls, raw_css_cls = _ORIG_IMPORT_WEASYPRINT()
				base_html_cls = _ORIG_WP_HTML if _ORIG_WP_HTML is not None else raw_html_cls
				base_css_cls = _ORIG_WP_CSS if _ORIG_WP_CSS is not None else raw_css_cls
				return _build_safe_weasyprint_factories(base_html_cls, base_css_cls, safe_fetcher)

			_wrapped_import_weasyprint._cskh_pdf_wrapped = True  # type: ignore[attr-defined]
			frappe_weasyprint.import_weasyprint = _wrapped_import_weasyprint


def ensure_pdf_runtime_hooks(*_args, **_kwargs) -> None:
	"""Thread-safely and idempotently install PDF security/asset hooks for `wkhtmltopdf` & `WeasyPrint`."""
	global _HOOKS_INSTALLED
	if _HOOKS_INSTALLED:
		return
	with _HOOKS_LOCK:
		if _HOOKS_INSTALLED:
			return
		_install_pdf_runtime_hooks_locked()
		_HOOKS_INSTALLED = True


def on_print_pdf(*_args, **_kwargs) -> None:
	"""Frappe `on_print_pdf` hook ensuring CLI / `bench execute` callers also activate adapters."""
	ensure_pdf_runtime_hooks()


def _inspect_pdf_bytes_summary(raw_pdf: bytes) -> dict[str, Any]:
	from pypdf import PdfReader

	reader = PdfReader(io.BytesIO(raw_pdf))
	page_texts = [unicodedata.normalize("NFC", p.extract_text() or "") for p in reader.pages]
	compact_all = "".join("".join(t.split()) for t in page_texts)

	fonts: list[str] = []
	tounicode_count = 0
	image_count = 0
	seen: set[int] = set()

	def _walk_res(res_obj) -> None:
		nonlocal tounicode_count, image_count
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
					tounicode_count += 1
		xdict = res.get("/XObject")
		if xdict:
			xdict = xdict.get_object() if hasattr(xdict, "get_object") else xdict
			for _, xref in xdict.items():
				xo = xref.get_object() if hasattr(xref, "get_object") else xref
				sub = str(xo.get("/Subtype") or "")
				if sub == "/Image":
					image_count += 1
				elif sub == "/Form":
					_walk_res(xo.get("/Resources"))

	for pg in reader.pages:
		_walk_res(pg.get("/Resources"))

	unique_fonts = sorted(set(fonts))
	return {
		"valid_pdf_envelope": raw_pdf.startswith(b"%PDF-1.") and b"%%EOF" in raw_pdf[-128:],
		"byte_length": len(raw_pdf),
		"page_count": len(reader.pages),
		"dejavu_font_embedded": any("dejavu" in f.lower() for f in unique_fonts),
		"tounicode_cmap_count": tounicode_count,
		"image_xobject_count": image_count,
		"compact_text_sample": compact_all[:300],
		"contains_vietnamese_title": "BÁOCÁOCHẤTLƯỢNGDỊCHVỤCSKH" in compact_all,
	}


def run_rq_pdf_verification_job(
	*,
	doctype: str,
	name: str,
	wkhtmltopdf_format: str,
	weasyprint_format: str,
	letterhead: str | None = None,
	authorized_user: str = "Administrator",
	unauthorized_user: str | None = None,
	private_file_url: str | None = None,
	result_cache_key: str | None = None,
) -> dict[str, Any]:
	"""Internal non-whitelisted RQ worker job verifying real `before_job` PDF rendering & RBAC."""
	hooks_preinstalled_by_before_job = bool(_HOOKS_INSTALLED)
	ensure_pdf_runtime_hooks()

	from frappe.utils.pdf import get_pdf
	from frappe.utils.weasyprint import PrintFormatGenerator, import_weasyprint

	orig_user = getattr(frappe.session, "user", "Administrator")
	try:
		frappe.set_user(authorized_user)
		wk_pdf = frappe.get_print(
			doctype,
			name,
			print_format=wkhtmltopdf_format,
			letterhead=letterhead,
			as_pdf=True,
		)
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("print")
		wp_pdf = PrintFormatGenerator(weasyprint_format, doc, letterhead).render_pdf()

		rbac_probe: dict[str, Any] = {}
		if private_file_url:
			probe_html = (
				'<html><head><style>@import "http://127.0.0.1:9/blocked.css"; '
				'body { font-family: "DejaVu Sans", sans-serif; }</style></head><body>'
				"<p>BÁO CÁO CHẤT LƯỢNG DỊCH VỤ CSKH</p>"
				f'<img src="{private_file_url}" width="32" height="32"/>'
				'<img src="https://evil.example.invalid/ssrf.png"/>'
				'<img src="//evil.example.invalid/files/proto.png"/>'
				"</body></html>"
			)
			html_cls, css_cls = import_weasyprint()
			auth_wk = get_pdf(probe_html)
			auth_wp = html_cls(string=probe_html).write_pdf(
				stylesheets=[css_cls(string='@import "//evil.example.invalid/a.css"; @page { size: A4; }')]
			)
			rbac_probe["authorized_wkhtmltopdf_images"] = _inspect_pdf_bytes_summary(auth_wk)[
				"image_xobject_count"
			]
			rbac_probe["authorized_weasyprint_images"] = _inspect_pdf_bytes_summary(auth_wp)[
				"image_xobject_count"
			]

			if unauthorized_user:
				frappe.set_user(unauthorized_user)
				unauth_wk = get_pdf(probe_html)
				unauth_wp = html_cls(string=probe_html).write_pdf()
				# Transparent 1x1 GIF fallback is 1x1, whereas private badge is >= 32x32; inspect dimensions!
				from pypdf import PdfReader

				def _large_images(pdf_b: bytes) -> int:
					rdr = PdfReader(io.BytesIO(pdf_b))
					cnt = 0
					for p in rdr.pages:
						res = p.get("/Resources")
						res = res.get_object() if hasattr(res, "get_object") else (res or {})
						xd = res.get("/XObject")
						xd = xd.get_object() if hasattr(xd, "get_object") else (xd or {})
						for _, xr in xd.items():
							xo = xr.get_object() if hasattr(xr, "get_object") else xr
							if str(xo.get("/Subtype") or "") == "/Image" and int(xo.get("/Width") or 0) >= 16:
								cnt += 1
					return cnt

				rbac_probe["authorized_large_images_wk"] = _large_images(auth_wk)
				rbac_probe["authorized_large_images_wp"] = _large_images(auth_wp)
				rbac_probe["unauthorized_large_images_wk"] = _large_images(unauth_wk)
				rbac_probe["unauthorized_large_images_wp"] = _large_images(unauth_wp)

		summary = {
			"status": "PASS",
			"worker_hostname": socket.gethostname(),
			"worker_pid": os.getpid(),
			"hooks_preinstalled_by_before_job": hooks_preinstalled_by_before_job,
			"wkhtmltopdf": _inspect_pdf_bytes_summary(wk_pdf),
			"weasyprint": _inspect_pdf_bytes_summary(wp_pdf),
			"private_file_and_ssrf_probe": rbac_probe,
		}
		if result_cache_key:
			frappe.cache.set_value(result_cache_key, summary, expires_in_sec=600)
		return summary
	finally:
		frappe.set_user(orig_user)
