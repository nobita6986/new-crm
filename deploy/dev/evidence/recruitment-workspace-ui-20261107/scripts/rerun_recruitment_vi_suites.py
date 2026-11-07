#!/usr/bin/env python3
"""Reproducible end-to-end rerun & evidence packager for Recruitment & Staffing Vietnamese UI/i18n (`6912c78`).

Usage (from repository root):
    python deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/rerun_recruitment_vi_suites.py

Isolation guarantees:
  * Uses dedicated Compose project `cskh-recruit-ui-verify` bound exclusively to `127.0.0.1:18620`.
  * Mounts `apps/crm_cskh/crm_cskh` read-only (`:ro`) onto pinned `crm-cskh:0.0.1-92e14af`.
  * Never touches VPS or any other host Docker containers/volumes.
  * Always tears down `cskh-recruit-ui-verify` (`down -v --remove-orphans`) and deletes temp credentials in `finally`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

SCRIPTS_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = SCRIPTS_DIR.parent
REPO_ROOT = BUNDLE_DIR.parents[3]
LOGS_DIR = BUNDLE_DIR / "logs"
JUNIT_DIR = BUNDLE_DIR / "junit"
SCREENSHOTS_DIR = BUNDLE_DIR / "screenshots"
COMPOSE_FILE = SCRIPTS_DIR / "docker-compose.recruit-ui.yml"
COMPOSE_PROJECT = "cskh-recruit-ui-verify"
WEB_CONTAINER = f"{COMPOSE_PROJECT}-web-1"
BASE_IMAGE = os.environ.get("CSKH_VERIFY_IMAGE", "crm-cskh:0.0.1-92e14af")


def write_lf(path: Path, text: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	normalized = text.replace("\r\n", "\n").replace("\r", "\n")
	if not normalized.endswith("\n"):
		normalized += "\n"
	path.write_bytes(normalized.encode("utf-8"))


def run_capture(cmd: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> tuple[int, str, float]:
	t0 = time.monotonic()
	merged_env = os.environ.copy()
	merged_env["PYTHONUTF8"] = "1"
	merged_env["PYTHONIOENCODING"] = "utf-8"
	if env:
		merged_env.update(env)
	proc = subprocess.run(
		cmd,
		cwd=str(cwd),
		env=merged_env,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		encoding="utf-8",
		errors="replace",
	)
	elapsed = round(time.monotonic() - t0, 3)
	return proc.returncode, proc.stdout or "", elapsed


def check_out(cmd: list[str], *, cwd: Path = REPO_ROOT) -> str:
	rc, out, _ = run_capture(cmd, cwd=cwd)
	if rc != 0:
		raise RuntimeError(f"Command failed (exit={rc}): {' '.join(cmd)}\n{out}")
	return out.strip()


def parse_junit_summary(xml_path: Path) -> dict[str, int | float]:
	root = ET.parse(xml_path).getroot()
	suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
	tests = sum(int(s.attrib.get("tests", 0)) for s in suites)
	failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
	errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
	skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
	duration = round(sum(float(s.attrib.get("time", 0.0) or 0.0) for s in suites), 3)
	return {
		"tests": tests,
		"passed": tests - failures - errors - skipped,
		"failures": failures,
		"errors": errors,
		"skipped": skipped,
		"junit_time_sec": duration,
	}


def rebuild_sha256_manifest() -> int:
	manifest_path = BUNDLE_DIR / "SHA256SUMS.txt"
	entries: list[str] = []
	for file_path in sorted(BUNDLE_DIR.rglob("*")):
		if (
			not file_path.is_file()
			or file_path.name == "SHA256SUMS.txt"
			or "__pycache__" in file_path.parts
			or file_path.suffix == ".pyc"
		):
			continue
		rel_posix = file_path.relative_to(BUNDLE_DIR).as_posix()
		digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
		entries.append(f"{digest}  {rel_posix}")
	write_lf(manifest_path, "\n".join(entries))
	return len(entries)


def main() -> int:
	LOGS_DIR.mkdir(parents=True, exist_ok=True)
	JUNIT_DIR.mkdir(parents=True, exist_ok=True)
	SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

	head_sha = check_out(["git", "rev-parse", "HEAD"])
	tested_code_sha = check_out(
		["git", "log", "-n", "1", "--format=%H", "--", "apps/", "deploy/p1/", "deploy/p2/", "deploy/p3/"]
	)
	crm_cskh_tree_sha = check_out(["git", "rev-parse", "HEAD:apps/crm_cskh"])
	base_image_id = check_out(["docker", "image", "inspect", BASE_IMAGE, "--format", "{{.Id}}"])
	host_python_ver = sys.version.split()[0]
	host_node_ver = check_out(["node", "--version"])

	suite_records: list[dict] = []
	temp_seed_file = Path(tempfile.gettempdir()) / f"cskh_recruit_ui_seed_{os.getpid()}.json"

	# -------------------------------------------------------------------------
	# PHASE 1: OFFLINE TRANSLATION, METRIC PARITY & CONTRACT GATES
	# -------------------------------------------------------------------------
	offline_suites = [
		(
			"01-check_translations",
			"python deploy/p1/check_translations.py apps/crm_cskh/crm_cskh/translations/vi.csv",
			[sys.executable, "deploy/p1/check_translations.py", "apps/crm_cskh/crm_cskh/translations/vi.csv"],
		),
		(
			"02-check_translations_audit",
			"python deploy/p1/check_translations_audit.py",
			[sys.executable, "deploy/p1/check_translations_audit.py"],
		),
		(
			"03-verify_p3_metric_parity",
			"python deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_p3_metric_parity.py",
			[
				sys.executable,
				"deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_p3_metric_parity.py",
			],
		),
		(
			"04-test_p2_ui_timezone_and_contracts",
			"node deploy/p2/test_p2_ui_timezone_and_contracts.mjs",
			["node", "deploy/p2/test_p2_ui_timezone_and_contracts.mjs"],
		),
		(
			"05-test_p3_ui_timezone_and_contracts",
			"node deploy/p3/test_p3_ui_timezone_and_contracts.mjs",
			["node", "deploy/p3/test_p3_ui_timezone_and_contracts.mjs"],
		),
	]

	for slug, display_cmd, cmd in offline_suites:
		print(f"[OFFLINE] Running {slug}: {display_cmd}", flush=True)
		rc, out, sec = run_capture(cmd)
		log_rel = f"deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/{slug}.log"
		write_lf(LOGS_DIR / f"{slug}.log", out)
		if rc != 0:
			raise RuntimeError(f"Offline suite {slug} failed (exit={rc}):\n{out}")
		suite_records.append(
			{
				"id": slug,
				"category": "offline_gate",
				"command": display_cmd,
				"scope": "full",
				"status": "PASS",
				"exit_code": rc,
				"duration_sec": sec,
				"log": log_rel,
				"summary": out.strip().splitlines()[-1] if out.strip() else "OK",
			}
		)

	# -------------------------------------------------------------------------
	# PHASE 2: ISOLATED DOCKER RUNTIME (FRAPPE JUNIT SUITES + PLAYWRIGHT E2E)
	# -------------------------------------------------------------------------
	compose_base = [
		"docker",
		"compose",
		"-p",
		COMPOSE_PROJECT,
		"-f",
		str(COMPOSE_FILE),
	]

	try:
		print("[DOCKER] Starting isolated stack cskh-recruit-ui-verify...", flush=True)
		check_out([*compose_base, "down", "-v", "--remove-orphans"])
		check_out([*compose_base, "up", "-d"])

		check_out(["docker", "cp", str(SCRIPTS_DIR / "bootstrap_recruit_ui.sh"), f"{WEB_CONTAINER}:/tmp/bootstrap_recruit_ui.sh"])
		check_out(["docker", "cp", str(SCRIPTS_DIR / "seed_recruitment_sample.py"), f"{WEB_CONTAINER}:/tmp/seed_recruitment_sample.py"])
		check_out(["docker", "cp", str(SCRIPTS_DIR / "run_frappe_junit.py"), f"{WEB_CONTAINER}:/tmp/run_frappe_junit.py"])
		check_out(
			[
				"docker",
				"exec",
				WEB_CONTAINER,
				"bash",
				"-lc",
				"tr -d '\\r' < /tmp/run_frappe_junit.py > /tmp/exec_junit.py && "
				"tr -d '\\r' < /tmp/seed_recruitment_sample.py > /tmp/exec_seed.py && "
				"tr -d '\\r' < /tmp/bootstrap_recruit_ui.sh > /tmp/exec_bootstrap.sh && bash /tmp/exec_bootstrap.sh",
			]
		)

		frappe_sha = check_out(
			["docker", "exec", WEB_CONTAINER, "git", "-C", "/home/frappe/frappe-bench/apps/frappe", "rev-parse", "HEAD"]
		)
		crm_sha = check_out(
			["docker", "exec", WEB_CONTAINER, "git", "-C", "/home/frappe/frappe-bench/apps/crm", "rev-parse", "HEAD"]
		)
		container_py_ver = check_out(
			["docker", "exec", WEB_CONTAINER, "/home/frappe/frappe-bench/env/bin/python", "--version"]
		).replace("Python ", "")

		runtime_revision = {
			"testedCodeSha": tested_code_sha,
			"checkoutHeadSha": head_sha,
			"crmCskhTreeSha": crm_cskh_tree_sha,
			"frappeSha": frappe_sha,
			"crmSha": crm_sha,
			"baseImage": BASE_IMAGE,
			"baseImageId": base_image_id,
			"containerPythonVersion": container_py_ver,
			"hostPythonVersion": host_python_ver,
			"hostNodeVersion": host_node_ver,
		}

		unit_modules = [
			("06-test_ui_p1_endpoints", "crm_cskh.tests.test_ui_p1_endpoints", False),
			("07-test_ui_p2_pages_and_context", "crm_cskh.tests.test_ui_p2_pages_and_context", False),
			("08-test_ui_p3_pages_and_context", "crm_cskh.tests.test_ui_p3_pages_and_context", False),
			("09-test_p2_outcomes_policy", "crm_cskh.tests.test_p2_outcomes_policy", False),
			("10-test_p3_kpi_and_snapshots", "crm_cskh.tests.test_p3_kpi_and_snapshots", True),
			("11-test_p3_reporting_and_security", "crm_cskh.tests.test_p3_reporting_and_security", False),
		]

		check_out(["docker", "exec", WEB_CONTAINER, "mkdir", "-p", "/tmp/junit"])

		for slug, module_name, wipe_before in unit_modules:
			short_mod = module_name.rsplit(".", 1)[-1]
			if wipe_before:
				print("[FRAPPE] Wiping isolated site cleanly before canonical P3 backend sequence...", flush=True)
				check_out(
					[
						"docker",
						"exec",
						WEB_CONTAINER,
						"/home/frappe/frappe-bench/env/bin/python",
						"/tmp/exec_seed.py",
						"recruit-ui.localhost",
						"--wipe-only",
					]
				)
			container_xml = f"/tmp/junit/{short_mod}.junit.xml"
			bench_cmd = (
				f"/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py "
				f"recruit-ui.localhost {module_name} {container_xml}"
			)
			display_cmd = (
				f"docker exec {WEB_CONTAINER} bash -lc \"{bench_cmd}\""
			)
			print(f"[FRAPPE] Running {slug} ({module_name})...", flush=True)
			rc, out, sec = run_capture(["docker", "exec", "-e", "CI=true", WEB_CONTAINER, "bash", "-lc", bench_cmd])
			log_path = LOGS_DIR / f"{slug}.log"
			write_lf(log_path, out)
			if rc != 0:
				raise RuntimeError(f"Frappe unit suite {module_name} failed (exit={rc}):\n{out}")

			local_xml = JUNIT_DIR / f"{short_mod}.junit.xml"
			check_out(["docker", "cp", f"{WEB_CONTAINER}:{container_xml}", str(local_xml)])
			write_lf(local_xml, local_xml.read_text(encoding="utf-8"))
			junit_stats = parse_junit_summary(local_xml)
			if junit_stats["failures"] != 0 or junit_stats["errors"] != 0 or junit_stats["tests"] == 0:
				raise RuntimeError(f"JUnit gate failed for {module_name}: {junit_stats}")

			suite_records.append(
				{
					"id": slug,
					"category": "frappe_unit_suite",
					"module": module_name,
					"command": display_cmd,
					"scope": f"full_module ({junit_stats['tests']}/{junit_stats['tests']} methods)",
					"status": "PASS",
					"exit_code": rc,
					"duration_sec": sec,
					"junit": f"deploy/dev/evidence/recruitment-workspace-ui-20261107/junit/{short_mod}.junit.xml",
					"log": f"deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/{slug}.log",
					**junit_stats,
				}
			)

		# Verify per-method JUnit coverage (48/48 methods across 6 modules) via canonical ci_junit_gate.py
		junit_gate_out = JUNIT_DIR / "junit_per_method_gate.json"
		print("[JUNIT-GATE] Verifying 48/48 methods via deploy/dev/seed/ci_junit_gate.py...", flush=True)
		check_out(
			[
				sys.executable,
				"deploy/dev/seed/ci_junit_gate.py",
				"--inventory",
				str(JUNIT_DIR / "junit_inventory.json"),
				"--xml-dir",
				str(JUNIT_DIR),
				"--out",
				str(junit_gate_out),
			]
		)
		write_lf(junit_gate_out, junit_gate_out.read_text(encoding="utf-8"))

		# Seed sample data + start bench worker & bench serve for Chromium E2E
		print("[SEED] Seeding synthetic recruitment + P3 sample data on recruit-ui.localhost...", flush=True)
		seed_json_raw = check_out(
			[
				"docker",
				"exec",
				WEB_CONTAINER,
				"bash",
				"-lc",
				"tr -d '\\r' < /tmp/seed_recruitment_sample.py > /tmp/exec_seed.py && "
				"/home/frappe/frappe-bench/env/bin/python /tmp/exec_seed.py recruit-ui.localhost",
			]
		)
		seed_payload = json.loads(seed_json_raw.splitlines()[-1])
		seed_payload["integratedSha"] = tested_code_sha
		seed_payload["runtimeRevision"] = runtime_revision
		write_lf(temp_seed_file, json.dumps(seed_payload, ensure_ascii=False, indent=2))

		print("[SERVE] Starting bench worker & bench serve on recruit-ui.localhost...", flush=True)
		check_out(
			[
				"docker",
				"exec",
				WEB_CONTAINER,
				"bash",
				"-lc",
				"cd /home/frappe/frappe-bench && "
				"nohup bench worker --queue short,default,long >/tmp/worker.log 2>&1 </dev/null & "
				"nohup bench serve --port 8000 >/tmp/serve.log 2>&1 </dev/null & "
				"for i in $(seq 1 30); do "
				"  if curl -fsS http://127.0.0.1:8000/api/method/ping >/dev/null 2>&1; then exit 0; fi; "
				"  sleep 1; "
				"done; cat /tmp/serve.log || true; exit 1",
			]
		)

		for stale_png in SCREENSHOTS_DIR.glob("*.png"):
			stale_png.unlink()

		browser_cmd_display = (
			"node deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_recruitment_ui_browser.mjs"
		)
		print(f"[PLAYWRIGHT] Running {browser_cmd_display}...", flush=True)
		rc, out, sec = run_capture(
			[
				"node",
				"deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_recruitment_ui_browser.mjs",
			],
			env={
				"RECRUIT_UI_SEED_JSON": str(temp_seed_file),
				"RECRUIT_UI_REPORT_JSON": str(BUNDLE_DIR / "browser_e2e_report.json"),
			},
		)
		write_lf(LOGS_DIR / "12-verify_recruitment_ui_browser.log", out)
		if rc != 0:
			raise RuntimeError(f"Browser E2E failed (exit={rc}):\n{out}")

		report_path = BUNDLE_DIR / "browser_e2e_report.json"
		write_lf(report_path, report_path.read_text(encoding="utf-8"))
		browser_report = json.loads(report_path.read_text(encoding="utf-8"))

		# Verify all 17 referenced screenshots exist on disk
		shots = browser_report.get("screenshots") or []
		if len(shots) != 17:
			raise RuntimeError(f"Expected 17 screenshots in browser_e2e_report.json, got {len(shots)}")
		for item in shots:
			rel_p = REPO_ROOT / item["path"]
			if not rel_p.is_file():
				raise RuntimeError(f"Missing referenced screenshot on disk: {item['path']}")

		suite_records.append(
			{
				"id": "12-verify_recruitment_ui_browser",
				"category": "browser_e2e",
				"command": browser_cmd_display,
				"scope": "full_e2e (8 check groups, 17 screenshots, 2 server CSV exports)",
				"status": "PASS",
				"exit_code": rc,
				"duration_sec": sec,
				"browser": browser_report.get("browser"),
				"screenshots_count": len(shots),
				"report": "deploy/dev/evidence/recruitment-workspace-ui-20261107/browser_e2e_report.json",
				"log": "deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/12-verify_recruitment_ui_browser.log",
			}
		)

		suite_summary = {
			"status": "PASS",
			"generatedAt": datetime.now(timezone.utc).isoformat(),
			"rerunEntryCommand": "python deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/rerun_recruitment_vi_suites.py",
			"runtimeRevision": runtime_revision,
			"browser": browser_report.get("browser"),
			"totals": {
				"suitesTotal": len(suite_records),
				"suitesPassed": sum(1 for s in suite_records if s["status"] == "PASS"),
				"suitesFailed": sum(1 for s in suite_records if s["status"] != "PASS"),
				"frappeUnitTestsTotal": sum(int(s.get("tests", 0)) for s in suite_records),
				"screenshotsTotal": len(shots),
			},
			"suites": suite_records,
			"p3ReportingAndSecurityRunComparison": [
				{
					"run": 1,
					"scope": "full_module (7 methods, standalone without predecessor test_p3_kpi_and_snapshots)",
					"result": "FAILED (5 passed, 1 error, 1 failure)",
					"log": "deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/historical-p3-reporting-runs/run1-standalone-siteconfig-override-7methods.log",
					"rootCause": (
						"(a) ERROR in test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled (KeyError: 'reporting_enabled'): "
						"ad-hoc bootstrap wrote p3_reporting_enabled=1 & p3_sla_enabled=1 into site_config.json (frappe.conf), "
						"which overrides CSKH Policy in services/p3_flags.py:16-18 when the test toggled CSKH Policy.p3_reporting_enabled=0. "
						"(b) FAIL in test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10 (overview['attempt_breakdown']['verified'] == 0): "
						"_seed_two_team_activity() calls p2_base.make_native_call_log() without telephony_medium, which SEC2-02 "
						"(call_log_adapter.py:117-123) classifies as 'self_reported'; in canonical CI (.github/workflows/p3.yml:128-129 & "
						"deploy/p3/run_suites.py:75), test_p3_kpi_and_snapshots runs immediately prior on the same site and seeds a "
						"verification_status='verified' attempt in the active month_key."
					),
				},
				{
					"run": 2,
					"scope": "full_module (7 methods, standalone after removing site_config.json p3_*_enabled overrides)",
					"result": "FAILED (6 passed, 0 errors, 1 failure)",
					"log": "deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/historical-p3-reporting-runs/run2-standalone-clean-siteconfig-7methods.log",
					"rootCause": (
						"Removing p3_*_enabled from site_config.json resolved test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled (PASS). "
						"Standalone execution without predecessor test_p3_kpi_and_snapshots still left verified == 0 in "
						"test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10."
					),
				},
				{
					"run": 3,
					"scope": "partial_single_method (--test test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10, 1 method)",
					"result": "FAILED (0 passed, 1 failure)",
					"log": "deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/historical-p3-reporting-runs/run3-single-method-bod-s08-s09-s10.log",
					"rootCause": (
						"Diagnostic single-method rerun after re-applying ensure_p2_seeds/ensure_p3_seeds confirmed the failure was "
						"strictly the cross-module fixture dependency on test_p3_kpi_and_snapshots.py:305-311 rather than CSKH Policy seeds."
					),
				},
				{
					"run": 4,
					"scope": "full_module (7/7 methods in canonical CI sequence after test_p3_kpi_and_snapshots, clean site_config.json)",
					"result": "PASS (7 passed, 0 errors, 0 failures)",
					"junit": "deploy/dev/evidence/recruitment-workspace-ui-20261107/junit/test_p3_reporting_and_security.junit.xml",
					"log": "deploy/dev/evidence/recruitment-workspace-ui-20261107/logs/11-test_p3_reporting_and_security.log",
					"rootCause": "Canonical CI execution order with clean site_config.json passes all 7/7 methods with zero code edits.",
				},
			],
		}
		write_lf(BUNDLE_DIR / "suite_results.json", json.dumps(suite_summary, ensure_ascii=False, indent=2))

	finally:
		if temp_seed_file.exists():
			temp_seed_file.unlink()
		print("[CLEANUP] Tearing down isolated stack cskh-recruit-ui-verify...", flush=True)
		run_capture([*compose_base, "down", "-v", "--remove-orphans"])

	hashed_count = rebuild_sha256_manifest()
	print(f"ALL SUITES PASSED! Generated SHA256SUMS.txt covering {hashed_count} files.", flush=True)
	return 0


if __name__ == "__main__":
	sys.exit(main())
