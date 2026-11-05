"""Unified feature-flag and site kill-switch resolver for P3 (`F-P3-08`, `P3.md` §13 & §15.3).

Single source of truth shared across:
  * Web/UI page gates & navigation (`crm_cskh.www.cskh_context`)
  * SLA obligation lifecycle & mutations (`crm_cskh.services.sla_engine`, `crm_cskh.api.sla`)
  * KPI reporting, recompute & export (`crm_cskh.services.report_query`, `kpi_runs`, `p3_export`)
  * QA sampling & review (`crm_cskh.services.qa`, `crm_cskh.api.qa`)
  * Background cron & RQ workers (`crm_cskh.jobs.evaluate_sla`, `recompute_kpi`, `run_export`)

Precedence rules:
  1. **Site kill switch (`cskh_p3_enabled` / `p3_enabled` in `frappe.conf`):**
     When explicitly false (`0`, `false`, `off`, `no`), ALL P3 runtime features and queued
     background jobs are immediately disabled (`site=False`, `ui=False`, `sla=False`,
     `reporting=False`, `qa=False`) while preserving P1/P2 operations and lightweight
     catch-up markers (`CSKH Dirty Scope`, `CSKH Team Membership History`).
  2. **Module override in `frappe.conf`:**
     `p3_sla_enabled`, `p3_reporting_enabled`, `cskh_qa_enabled` / `qa_enabled` override
     `CSKH Policy` when explicitly present in `frappe.conf`.
  3. **Effective `CSKH Policy` flags:**
     `p3_sla_enabled` (default `True`), `p3_reporting_enabled` (default `True`),
     `qa_enabled` (default `False`).
"""

from __future__ import annotations

import frappe
from frappe.utils import cint


def parse_flag_bool(value, default: bool) -> bool:
	"""Coerce `site_config` / `CSKH Policy` flag values into a deterministic `bool`."""
	if value is None:
		return bool(default)
	if isinstance(value, bool):
		return value
	if isinstance(value, int | float):
		return value != 0
	text = str(value).strip().lower()
	if text in ("1", "true", "yes", "on"):
		return True
	if text in ("0", "false", "no", "off"):
		return False
	return bool(default)


def _conf() -> dict:
	raw = getattr(frappe, "conf", None)
	return raw if isinstance(raw, dict) else {}


def is_p3_site_enabled() -> bool:
	"""Master site-level kill switch (`cskh_p3_enabled` / `p3_enabled` in `site_config.json`)."""
	conf = _conf()
	for key in ("cskh_p3_enabled", "p3_enabled"):
		if key in conf and conf.get(key) is not None:
			return parse_flag_bool(conf.get(key), True)
	return True


def _resolve_policy_flag(
	field: str,
	default: bool,
	*,
	campaign: str | None = None,
	owner_user: str | None = None,
) -> bool:
	"""Read `field` from effective `CSKH Policy`, falling back safely to `default` before migration."""
	try:
		if not getattr(frappe, "db", None) or not frappe.db.table_exists("CSKH Policy"):
			return bool(default)
		if not frappe.db.has_column("CSKH Policy", field):
			return bool(default)
	except Exception:
		return bool(default)

	camp_val = str(campaign or "").strip() or None
	user_val = str(owner_user or "").strip() or None
	if camp_val or user_val:
		try:
			from crm_cskh.services.policies import resolve_effective_policy

			pol = resolve_effective_policy(campaign=camp_val, assignee=user_val)
			if isinstance(pol, dict) and field in pol and pol.get(field) is not None:
				return bool(cint(pol.get(field)))
		except Exception:
			pass

	try:
		global_val = frappe.db.get_value(
			"CSKH Policy",
			{"scope_type": "Global", "enabled": 1},
			field,
			order_by="version desc, modified desc",
		)
		if global_val is not None:
			return bool(cint(global_val))

		any_rows = frappe.db.sql(
			f"select max(`{field}`) from `tabCSKH Policy` where enabled = 1",
		)
		if any_rows and any_rows[0] and any_rows[0][0] is not None:
			return bool(cint(any_rows[0][0]))
	except Exception:
		return bool(default)
	return bool(default)


def is_p3_sla_enabled(
	*,
	campaign: str | None = None,
	owner_user: str | None = None,
) -> bool:
	"""Return `True` when P3 SLA obligation tracking & evaluation are active."""
	if not is_p3_site_enabled():
		return False
	conf = _conf()
	for key in ("cskh_p3_sla_enabled", "p3_sla_enabled"):
		if key in conf and conf.get(key) is not None:
			return parse_flag_bool(conf.get(key), True)
	return _resolve_policy_flag("p3_sla_enabled", True, campaign=campaign, owner_user=owner_user)


def is_p3_reporting_enabled(
	*,
	campaign: str | None = None,
	owner_user: str | None = None,
) -> bool:
	"""Return `True` when P3 KPI reporting, recompute & export are active."""
	if not is_p3_site_enabled():
		return False
	conf = _conf()
	for key in ("cskh_p3_reporting_enabled", "p3_reporting_enabled"):
		if key in conf and conf.get(key) is not None:
			return parse_flag_bool(conf.get(key), True)
	return _resolve_policy_flag("p3_reporting_enabled", True, campaign=campaign, owner_user=owner_user)


def is_p3_export_enabled(
	*,
	campaign: str | None = None,
	owner_user: str | None = None,
) -> bool:
	"""Return `True` when P3 report export is active."""
	if not is_p3_site_enabled():
		return False
	conf = _conf()
	for key in ("cskh_p3_export_enabled", "p3_export_enabled"):
		if key in conf and conf.get(key) is not None:
			return parse_flag_bool(conf.get(key), True)
	return is_p3_reporting_enabled(campaign=campaign, owner_user=owner_user)


def is_qa_enabled(
	*,
	campaign: str | None = None,
	owner_user: str | None = None,
	policy_cfg: dict | None = None,
) -> bool:
	"""Return `True` when optional P3 QA sampling & review module is active (default `False`)."""
	if not is_p3_site_enabled():
		return False
	conf = _conf()
	for key in ("cskh_qa_enabled", "qa_enabled"):
		if key in conf and conf.get(key) is not None:
			return parse_flag_bool(conf.get(key), False)
	if (
		isinstance(policy_cfg, dict)
		and "qa_enabled" in policy_cfg
		and policy_cfg.get("qa_enabled") is not None
	):
		return bool(cint(policy_cfg.get("qa_enabled")))
	return _resolve_policy_flag("qa_enabled", False, campaign=campaign, owner_user=owner_user)


def is_p3_ui_enabled(*, route: str | None = None) -> bool:
	"""Return `True` when P3 `/cskh/*` web screens & navigation should be enabled."""
	if not is_p3_site_enabled():
		return False
	conf = _conf()
	has_sla_conf = any(
		k in conf and conf.get(k) is not None for k in ("cskh_p3_sla_enabled", "p3_sla_enabled")
	)
	has_rep_conf = any(
		k in conf and conf.get(k) is not None for k in ("cskh_p3_reporting_enabled", "p3_reporting_enabled")
	)
	if has_sla_conf and not has_rep_conf and not is_p3_sla_enabled():
		return False
	route_key = str(route or "").strip().strip("/").rsplit("/", 1)[-1]
	if route_key == "sla-operations":
		return is_p3_sla_enabled() or is_p3_reporting_enabled()
	if route_key in (
		"quality-overview",
		"team-quality",
		"employee-quality",
		"campaign-quality",
		"scorecard",
	):
		return is_p3_reporting_enabled()
	return is_p3_reporting_enabled() or is_p3_sla_enabled()


def resolve_p3_flags_snapshot(
	*,
	campaign: str | None = None,
	owner_user: str | None = None,
) -> dict[str, bool]:
	"""Return all effective P3 flags as a single deterministic dictionary."""
	site_on = is_p3_site_enabled()
	sla_on = is_p3_sla_enabled(campaign=campaign, owner_user=owner_user) if site_on else False
	rep_on = is_p3_reporting_enabled(campaign=campaign, owner_user=owner_user) if site_on else False
	exp_on = is_p3_export_enabled(campaign=campaign, owner_user=owner_user) if site_on else False
	qa_on = is_qa_enabled(campaign=campaign, owner_user=owner_user) if site_on else False
	return {
		"p3_enabled": site_on,
		"p3_site_enabled": site_on,
		"p3_ui_enabled": bool(site_on and (rep_on or sla_on)),
		"p3_sla_enabled": sla_on,
		"p3_reporting_enabled": rep_on,
		"p3_export_enabled": exp_on,
		"qa_enabled": qa_on,
	}


get_effective_p3_flags = resolve_p3_flags_snapshot
resolve_p3_flags = resolve_p3_flags_snapshot
