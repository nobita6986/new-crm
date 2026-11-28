"""CRM CSKH custom app."""

__version__ = "0.0.1"

try:
	from crm_cskh.services import runtime_cache as _runtime_cache

	_runtime_cache._ensure_mutation_hooks_installed()
except Exception:
	pass

try:
	from crm_cskh.services import pdf_hooks as _pdf_hooks

	_pdf_hooks.ensure_pdf_runtime_hooks()
except Exception:
	pass
