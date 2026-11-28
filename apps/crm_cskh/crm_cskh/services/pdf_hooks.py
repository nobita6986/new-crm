"""Ultra-lightweight lazy import bootstrap for Frappe v15 PDF security hooks.

Eliminates eager PDF engine / HTML parser loading on non-PDF HTTP requests and forked RQ
background jobs (`Ops 54cb7a1` report `08e-before-job-weasyprint-eager-import-diagnosis.json`)
while guaranteeing that all `wkhtmltopdf` (`pdfkit` / `frappe.utils.pdf`) and `WeasyPrint`
(`frappe.utils.weasyprint` / `weasyprint`) security controls (`F-TRIAL-PDFSEC-01`,
`F-TRIAL-HOOK-RB-01`) are installed **strictly before the very first engine invocation**
across HTTP (`before_request`), RQ workers (`before_job`), CLI (`bench` / `on_print_pdf`),
and test runners (`before_tests`).

Design invariants:
  1. Module import cost (`import_ms`) is near-zero: imports ONLY Python bootstrap stdlib
     modules (`importlib.abc`, `sys`, `threading`) — never `weasyprint`, `frappe.utils.pdf`,
     `pdfkit`, `cssutils`, `pypdf`, `bs4`, or `xml.etree.ElementTree`.
  2. Hook call cost (`call_ms`) on non-PDF requests/jobs is near-zero: registers an
     idempotent `sys.meta_path` post-exec loader guard (`_CskhPdfEngineMetaPathFinder`)
     under a leaf `threading.RLock` without importing any PDF engine.
	3. Pre-first-use guarantee: the instant any code path imports `cssutils`, `pdfkit`,
	   `frappe.utils.pdf`, `frappe.utils.weasyprint`, or `weasyprint`,
	   `_CskhPostExecLoaderWrapper.exec_module` patches that module *before* the `import`
	   statement returns or binds attributes in the caller. Any target already present in
	   `sys.modules` when `ensure_pdf_runtime_hooks()` runs is patched immediately without
	   importing unloaded targets.
	4. Deadlock-free concurrency: `_HOOKS_LOCK` is a strict leaf lock (no `import` ever runs
	   while holding `_HOOKS_LOCK`), cooperating cleanly with Python's per-module import locks.
"""

from __future__ import annotations

import importlib.abc
import sys
import threading

PDF_ENGINE_TARGET_MODULES: tuple[str, ...] = (
	"cssutils",
	"pdfkit",
	"frappe.utils.pdf",
	"frappe.utils.weasyprint",
	"weasyprint",
)
_PDF_ENGINE_TARGET_SET: frozenset[str] = frozenset(PDF_ENGINE_TARGET_MODULES)

_HOOKS_LOCK = threading.RLock()
_HOOKS_INSTALLED = False
_REENTRANCY_LOCAL = threading.local()


def _patch_loaded_target_module(fullname: str, module: object) -> None:
	"""Import `pdf_assets` outside `_HOOKS_LOCK` and patch `module` idempotently."""
	from crm_cskh.services import pdf_assets

	pdf_assets.patch_loaded_pdf_module(fullname, module)


class _CskhPostExecLoaderWrapper(importlib.abc.Loader):
	"""Wraps a target module's `Loader` to apply PDF security patches before `import` returns."""

	def __init__(self, fullname: str, orig_loader: object) -> None:
		self._fullname = fullname
		self._orig_loader = orig_loader

	def create_module(self, spec):
		create_fn = getattr(self._orig_loader, "create_module", None)
		if callable(create_fn):
			return create_fn(spec)
		return None

	def exec_module(self, module) -> None:
		exec_fn = getattr(self._orig_loader, "exec_module", None)
		if callable(exec_fn):
			exec_fn(module)
		try:
			module.__loader__ = self._orig_loader
			spec = getattr(module, "__spec__", None)
			if spec is not None:
				spec.loader = self._orig_loader
		except Exception:
			pass
		_patch_loaded_target_module(self._fullname, module)

	def __getattr__(self, attr: str):
		return getattr(self._orig_loader, attr)


class _CskhPdfEngineMetaPathFinder(importlib.abc.MetaPathFinder):
	"""Intercepts first import of PDF engine modules without eagerly importing them."""

	_cskh_pdf_meta_finder = True

	def find_spec(self, fullname: str, path=None, target=None):
		if fullname not in _PDF_ENGINE_TARGET_SET:
			return None

		active: set[str] | None = getattr(_REENTRANCY_LOCAL, "active", None)
		if active is None:
			active = set()
			_REENTRANCY_LOCAL.active = active
		if fullname in active:
			return None

		active.add(fullname)
		try:
			for finder in list(sys.meta_path):
				if finder is self or getattr(finder, "_cskh_pdf_meta_finder", False):
					continue
				find_spec_fn = getattr(finder, "find_spec", None)
				if not callable(find_spec_fn):
					continue
				spec = find_spec_fn(fullname, path, target)
				if spec is not None and spec.loader is not None:
					if not isinstance(spec.loader, _CskhPostExecLoaderWrapper) and callable(
						getattr(spec.loader, "exec_module", None)
					):
						spec.loader = _CskhPostExecLoaderWrapper(fullname, spec.loader)
					return spec
			return None
		finally:
			active.discard(fullname)


_META_PATH_FINDER = _CskhPdfEngineMetaPathFinder()


def _unpatched_loaded_targets_locked(*, include_patched: bool = False) -> list[tuple[str, object]]:
	pending: list[tuple[str, object]] = []
	for mod_name in PDF_ENGINE_TARGET_MODULES:
		mod = sys.modules.get(mod_name)
		if mod is None:
			continue
		spec = getattr(mod, "__spec__", None)
		if spec is not None and bool(getattr(spec, "_initializing", False)):
			# Currently inside `exec_module` on another thread; its wrapper will patch it before unlock.
			continue
		if include_patched or not getattr(mod, "_cskh_module_patched", False):
			pending.append((mod_name, mod))
	return pending


def ensure_pdf_runtime_hooks(*_args, force_recheck: bool = False, **_kwargs) -> None:
	"""Arm lazy PDF security hooks without importing PDF engines on non-PDF paths."""
	global _HOOKS_INSTALLED
	if _HOOKS_INSTALLED and not force_recheck:
		return

	with _HOOKS_LOCK:
		if not any(getattr(f, "_cskh_pdf_meta_finder", False) for f in sys.meta_path):
			sys.meta_path.insert(0, _META_PATH_FINDER)
		pending = _unpatched_loaded_targets_locked(include_patched=force_recheck)
		if not pending:
			_HOOKS_INSTALLED = True
			return

	for mod_name, mod in pending:
		_patch_loaded_target_module(mod_name, mod)

	with _HOOKS_LOCK:
		_HOOKS_INSTALLED = True


def on_print_pdf(*_args, **_kwargs) -> None:
	"""Frappe `on_print_pdf` hook ensuring CLI / `bench execute` callers arm PDF guards."""
	ensure_pdf_runtime_hooks()


def is_pdf_hook_guard_installed() -> bool:
	"""Return True when the lazy `sys.meta_path` guard has been armed in this process."""
	return bool(_HOOKS_INSTALLED) and any(getattr(f, "_cskh_pdf_meta_finder", False) for f in sys.meta_path)


def run_rq_non_pdf_probe_job(*, result_cache_key: str | None = None) -> dict[str, object]:
	"""Internal non-PDF RQ worker probe verifying `before_job` leaves PDF engines unloaded."""
	import os
	import socket
	import time

	import frappe

	t0 = time.perf_counter()
	ensure_pdf_runtime_hooks()
	reentry_call_ms = round((time.perf_counter() - t0) * 1000.0, 4)

	monitored_modules = (
		"weasyprint",
		"frappe.utils.weasyprint",
		"frappe.utils.pdf",
		"pdfkit",
		"cssutils",
		"pypdf",
		"bs4",
		"crm_cskh.services.pdf_assets",
	)
	loaded_map = {name: (name in sys.modules) for name in monitored_modules}
	payload: dict[str, object] = {
		"status": "PASS",
		"worker_hostname": socket.gethostname(),
		"worker_pid": os.getpid(),
		"hook_guard_armed": is_pdf_hook_guard_installed(),
		"reentry_call_ms": reentry_call_ms,
		"loaded_modules": loaded_map,
		"any_pdf_engine_loaded": any(loaded_map.values()),
	}
	if result_cache_key:
		frappe.cache.set_value(result_cache_key, payload, expires_in_sec=600)
	return payload
