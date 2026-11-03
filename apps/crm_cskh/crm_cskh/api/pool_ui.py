"""P1 API: the Customer Pool view of P1 §9.1 (`crm_cskh.api.pool_ui.list`).

Shares a single source of truth with `crm_cskh.services.reporting` for all scope,
authorization, batch-link, and SQL pre-pagination predicates (`SEC-07`, `L-23`,
`L-25`, `L-26`) so `pool_ui.list`, `customers.customer_pool`, and
`customers.pool_list` enforce identical cross-team isolation and positive controls
without maintaining duplicate SQL copies.
"""

import frappe

from crm_cskh.api._ui_params import parse_optional_str, parse_pagination
from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden

POOL_ROLES = {"Sales Manager", "CSKH CRM Admin", "CSKH BOD Viewer"}
ALLOWED_STATES = reporting.ALLOWED_POOL_STATES


def _roles(user):
	return set(frappe.get_roles(user))


def _scoped_wi_predicate(scope, alias="wi"):
	"""Delegate work-item scope predicate directly to `reporting` (`L-23`)."""
	return reporting._scoped_wi_predicate(scope, alias)


def _scoped_ac_predicate(scope, alias="ac"):
	"""Delegate active-claim scope predicate directly to `reporting` (`L-23`)."""
	return reporting._scoped_ac_predicate(scope, alias)


def _team_lead_authorization_clauses(scope, lead_alias="l"):
	"""Delegate current team authorization on `CRM Lead` directly to `reporting` (`L-23`)."""
	return reporting._team_lead_authorization_clauses(scope, lead_alias)


def filter_team_authorized_leads(scope, candidate_leads):
	"""Delegate candidate-lead authorization directly to `reporting` (`L-23`, `L-24`)."""
	return reporting.filter_team_authorized_leads(scope, candidate_leads)


def _batch_lead_predicate(batch_val, lead_alias="l"):
	"""Delegate `Import Batch ↔ Lead` predicate directly to `reporting` (`L-25`)."""
	return reporting.lead_in_batch_clause(batch_val, f"{lead_alias}.name")


def _batch_wi_predicate(batch_val, wi_alias="wi"):
	"""Delegate `Import Batch ↔ Work Item` predicate directly to `reporting` (`L-25`)."""
	return reporting.work_item_in_batch_clause(batch_val, wi_alias)


@frappe.whitelist(methods=["GET"])
def list(page=1, page_size=20, campaign=None, state=None, source=None, batch=None, search=None):
	"""Return the scoped Customer Pool with derived distribution states (P1 §9.1)."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not (_roles(actor) & POOL_ROLES):
			raise Forbidden("You may not read the customer pool.")

	page_num, limit, _ = parse_pagination(page, page_size)
	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	state_val = parse_optional_str(state)
	source_val = parse_optional_str(source)
	batch_val = parse_optional_str(batch)
	search_val = parse_optional_str(search)

	return reporting.customer_pool(
		actor,
		page=page_num,
		page_size=limit,
		campaign=campaign_val or None,
		state=state_val or None,
		source=source_val or None,
		batch=batch_val or None,
		search=search_val or None,
	)
