"""CRM CSKH custom app."""

__version__ = "0.0.1"

try:
	from crm_cskh.services import runtime_cache as _runtime_cache

	_runtime_cache._ensure_mutation_hooks_installed()
except Exception:
	pass
