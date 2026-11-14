"""Dedicated, opt-in diagnostic runner for the synthetic P4 G18 distribution job.

Create the benchmark run with `enqueue_job=False`, then invoke this callable as a
separate one-shot RQ job on the isolated `p4-s1-bench.localhost` queue. This
entrypoint is deliberately separate from the ordinary distribution worker.
"""

from __future__ import annotations

import json
import os

import frappe

from crm_cskh.p4_diagnostics import profile_database_sql
from crm_cskh.services import distribution

_DIAGNOSTIC_FLAG = "CSKH_P4_G18_DISTRIBUTION_DIAGNOSTICS"
_DIAGNOSTIC_RUN_ALLOWLIST = "CSKH_P4_G18_DISTRIBUTION_DIAGNOSTIC_RUN_ID"
_DIAGNOSTIC_SITE_ALLOWLIST = "CSKH_P4_G18_DISTRIBUTION_DIAGNOSTIC_SITE"
_REQUIRED_SITE = "p4-s1-bench.localhost"
_SYNTHETIC_CAMPAIGN_CODE = "S1-CAMP-DIST-10K"


def _require_isolated_synthetic_run(run_id: str, actor: str | None) -> None:
	if os.environ.get(_DIAGNOSTIC_FLAG) != "1":
		raise RuntimeError("G18 distribution diagnostics are disabled.")
	if os.environ.get(_DIAGNOSTIC_SITE_ALLOWLIST) != _REQUIRED_SITE:
		raise RuntimeError("G18 distribution diagnostics require the isolated benchmark site.")
	if getattr(frappe.local, "site", None) != _REQUIRED_SITE:
		raise RuntimeError("G18 distribution diagnostics refused this site.")
	if not run_id or os.environ.get(_DIAGNOSTIC_RUN_ALLOWLIST) != run_id:
		raise RuntimeError("G18 distribution diagnostics require the exact allowlisted run id.")
	if actor != "Administrator":
		raise RuntimeError("G18 distribution diagnostics require the synthetic Administrator actor.")

	run = frappe.get_doc("CSKH Distribution Run", run_id)
	campaign_code = frappe.db.get_value("CSKH Campaign", run.campaign, "code")
	if campaign_code != _SYNTHETIC_CAMPAIGN_CODE:
		raise RuntimeError("G18 distribution diagnostics refused a non-synthetic campaign.")
	if str(run.status or "").lower() != "queued" or int(run.progress or 0) != 0:
		raise RuntimeError("G18 distribution diagnostics require a fresh queued run.")
	try:
		preview = json.loads(run.preview_json or "{}")
		actual = json.loads(run.actual_json or "{}")
	except (TypeError, ValueError) as exc:
		raise RuntimeError("G18 distribution diagnostics require valid benchmark run metadata.") from exc
	if int(preview.get("eligible_count") or 0) != 10_000 or actual.get("actor") != actor:
		raise RuntimeError("G18 distribution diagnostics refused non-benchmark run metadata.")

	from frappe.utils.background_jobs import generate_qname
	from redis import Redis
	from rq.exceptions import NoSuchJobError
	from rq.job import Job

	connection = Redis.from_url(frappe.conf.redis_queue)
	job_id = generate_qname(f"cskh-dist-{run_id}")
	try:
		Job.fetch(job_id, connection=connection)
		raise RuntimeError("G18 diagnostic run already has a normal distribution RQ job.")
	except NoSuchJobError:
		pass


def run_distribution_with_diagnostics(run_id: str, actor: str = "Administrator"):
	"""Run the unchanged domain worker with SQL fingerprint telemetry at chunk commits."""
	_require_isolated_synthetic_run(run_id, actor)
	logger = frappe.logger("crm_cskh.p4_g18_distribution_sql_diagnostic")

	def emit_checkpoint(recorder, progress: int | None) -> None:
		try:
			payload = recorder.snapshot(progress)
			logger.info(
				"CSKH_P4_G18_DIST_SQL_DIAG %s",
				json.dumps(payload, sort_keys=True, separators=(",", ":")),
			)
		except Exception:
			# Diagnostics must never interrupt a committed distribution chunk.
			return

	with profile_database_sql(frappe.db, run_id) as recorder:
		final_progress = None
		try:
			result = distribution.run_distribution_worker(
				run_id,
				actor=actor,
				diagnostics_callback=lambda progress: emit_checkpoint(recorder, progress),
			)
			if isinstance(result, dict):
				progress = result.get("progress")
				if isinstance(progress, int) and not isinstance(progress, bool):
					final_progress = progress
			return result
		finally:
			# Include early returns or errors that happen before the first chunk.
			emit_checkpoint(recorder, final_progress)
