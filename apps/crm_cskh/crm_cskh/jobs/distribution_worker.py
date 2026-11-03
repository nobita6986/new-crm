"""Background RQ worker for CSKH campaign distribution runs (P1 §7.5)."""

from __future__ import annotations

from crm_cskh.services import distribution


def run_distribution(run_id: str, actor: str | None = None):
	"""Execute or resume a queued `CSKH Distribution Run` in bounded chunks."""
	return distribution.run_distribution_worker(run_id, actor=actor)
