"""Background worker & hourly TTL reaper for `CSKH Export Job` (`P3-08`)."""

from __future__ import annotations

from crm_cskh.services.p3_export import cleanup_expired_exports, execute_export_job_now


def execute_export_job(export_id: str) -> dict:
	"""RQ background job entrypoint called after `request_export` commits."""
	return execute_export_job_now(export_id)


def expire_stale_exports() -> dict:
	"""Hourly scheduler job reaping expired export jobs & private CSV files."""
	return cleanup_expired_exports()
