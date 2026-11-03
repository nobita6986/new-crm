#!/usr/bin/env python3
"""Comprehensive real-HTTP E2E verification on the isolated P1 site (`p1.localhost`).

Drives the live Frappe HTTP server (`http://127.0.0.1:8120`, `Host: p1.localhost`)
across all 6 business accounts (`admin`, `leader_a`, `leader_b`, `staff_a`,
`staff_b`, `bod`) plus `outside` and `Guest`:
  1. CSV upload -> mapping (`phone`, `name`, `source`) -> preview -> confirm ->
     `COMPLETED_WITH_ERRORS` -> error CSV download (`200` Admin, `403` others).
  2. XLSX upload -> preview -> confirm -> `COMPLETED` + failed batch retry.
  3. Campaign create/edit/close + rules (`weight`, `capacity=0` Unlimited &
     capped) + stale preview (`RULE_CHANGED` 409 & `PREVIEW_EXPIRED` 409) +
     anti-double-submit idempotency + execute -> exact staff `my_list` delivery.
  4. Reassign & revoke -> old owner loses `my_list` & `CRM Lead` read (`403`),
     new owner gains access (`200`), stale `expected_owner` rejected (`CONFLICT`
     409), live `assigned_work_items` vs audit `assignment_events` reconciled.
  5. Team A vs Team B isolation, BOD aggregate read with zero PII (`phone=None`,
     `display_name=""`), CSRF enforcement, and `NOT_FOUND` (404) checks.

Saves sanitized evidence (no passwords, cookies, CSRF tokens, or unmasked phone
numbers) to `deploy/p1/evidence/http-e2e-report.json`.
"""

import base64
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar
from xml.sax.saxutils import escape as xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EVIDENCE_DIR = os.path.join(HERE, "evidence")
REPORT_PATH = os.path.join(EVIDENCE_DIR, "http-e2e-report.json")

BASE = os.environ.get("P1_BASE", "http://127.0.0.1:8120")
HOST = os.environ.get("P1_HOST", "p1.localhost")
PASSWORD = os.environ.get("P1_SEED_PASSWORD", "P1Seed-Only-2026!")
CONTAINER = os.environ.get("P1_CONTAINER", "crm-cskh-p1-bench-1")

PAGES = [
	"/cskh",
	"/cskh/pool",
	"/cskh/import",
	"/cskh/campaigns",
	"/cskh/my-customers",
	"/cskh/dashboard",
]

PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")


def redact(obj):
	"""Mask any Vietnamese phone numbers in strings/dicts/lists before writing evidence."""
	if isinstance(obj, str):
		return PHONE_RE.sub(lambda m: "******" + m.group(0)[-4:], obj)
	if isinstance(obj, list):
		return [redact(v) for v in obj]
	if isinstance(obj, dict):
		return {k: redact(v) for k, v in obj.items() if k not in ("csrf", "cookie", "password", "pwd")}
	return obj


def build_minimal_xlsx(rows):
	"""Build a standards-compliant `.xlsx` (OOXML inlineStr) in memory without external deps."""
	col_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
	sheet_rows_xml = []
	for r_idx, row in enumerate(rows, start=1):
		cells_xml = []
		for c_idx, val in enumerate(row):
			ref = f"{col_letters[c_idx]}{r_idx}"
			cells_xml.append(f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(str(val))}</t></is></c>')
		sheet_rows_xml.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

	buf = io.BytesIO()
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
		zf.writestr(
			"[Content_Types].xml",
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
			'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
			'<Default Extension="xml" ContentType="application/xml"/>'
			'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
			'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
			"</Types>",
		)
		zf.writestr(
			"_rels/.rels",
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
			'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
			"</Relationships>",
		)
		zf.writestr(
			"xl/workbook.xml",
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
			'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
			'<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
			"</workbook>",
		)
		zf.writestr(
			"xl/_rels/workbook.xml.rels",
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
			'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
			"</Relationships>",
		)
		zf.writestr(
			"xl/worksheets/sheet1.xml",
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
			f"<sheetData>{''.join(sheet_rows_xml)}</sheetData>"
			"</worksheet>",
		)
	return buf.getvalue()


def bench_execute(method, kwargs=None):
	cmd = f"cd /workspace/frappe-bench && bench --site p1.localhost execute {method}"
	if kwargs:
		cmd += f" --kwargs '{json.dumps(kwargs)}'"
	res = subprocess.run(
		["docker", "exec", CONTAINER, "bash", "-lc", cmd],
		capture_output=True,
		text=True,
		encoding="utf-8",
		check=True,
	)
	lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
	return json.loads(lines[-1])


class Session:
	def __init__(self, user=None):
		self.user = user or "Guest"
		self.jar = CookieJar()
		self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
		self.csrf = ""
		if user:
			self._login(user)

	def _login(self, user):
		req = urllib.request.Request(
			BASE + "/api/method/login",
			data=json.dumps({"usr": user, "pwd": PASSWORD}).encode("utf-8"),
			headers={"Host": HOST, "Content-Type": "application/json", "Accept": "application/json"},
			method="POST",
		)
		with self.opener.open(req, timeout=30) as resp:
			assert resp.status == 200, f"Login failed for {user}: {resp.status}"
			resp.read()
		self.refresh_csrf()

	def refresh_csrf(self):
		for probe_path in ("/cskh/my-customers", "/cskh"):
			status, raw = self.raw_get(probe_path)
			if status == 200:
				html = raw.decode("utf-8", errors="replace")
				for pattern in (
					r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)',
					r'csrf_token\s*=\s*["\']([^"\']+)',
					r'csrf-token"\s+content="([^"]+)"',
				):
					m = re.search(pattern, html)
					if m:
						self.csrf = m.group(1)
						return

	def raw_get(self, path):
		req = urllib.request.Request(BASE + path, headers={"Host": HOST}, method="GET")
		try:
			with self.opener.open(req, timeout=30) as resp:
				return resp.status, resp.read()
		except urllib.error.HTTPError as exc:
			return exc.code, exc.read()

	def get_api(self, method_name, params=None):
		qs = ""
		if params:
			clean = {
				k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
				for k, v in params.items()
				if v is not None and v != ""
			}
			if clean:
				qs = "?" + urllib.parse.urlencode(clean)
		status, raw = self.raw_get(f"/api/method/{method_name}{qs}")
		try:
			body = json.loads(raw.decode("utf-8"))
		except Exception:
			body = {"raw": raw.decode("utf-8", errors="replace")[:300]}
		return status, body

	def post_api(self, method_name, payload=None, csrf_override=None):
		headers = {
			"Host": HOST,
			"Content-Type": "application/json",
			"Accept": "application/json",
		}
		tok = self.csrf if csrf_override is None else csrf_override
		if tok:
			headers["X-Frappe-CSRF-Token"] = tok
		req = urllib.request.Request(
			f"{BASE}/api/method/{method_name}",
			data=json.dumps(payload or {}).encode("utf-8"),
			headers=headers,
			method="POST",
		)
		try:
			with self.opener.open(req, timeout=30) as resp:
				status, raw = resp.status, resp.read()
		except urllib.error.HTTPError as exc:
			status, raw = exc.code, exc.read()
		try:
			body = json.loads(raw.decode("utf-8"))
		except Exception:
			body = {"raw": raw.decode("utf-8", errors="replace")[:300]}
		return status, body


def poll_batch(admin, batch_name, timeout=45):
	deadline = time.time() + timeout
	while time.time() < deadline:
		st, body = admin.get_api("crm_cskh.api.imports.get_status", {"batch": batch_name})
		assert st == 200, f"get_status failed: {st} {body}"
		msg = body["message"]
		if msg["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
			return msg
		time.sleep(0.8)
	raise AssertionError(f"Batch {batch_name} timed out")


def main():
	os.makedirs(EVIDENCE_DIR, exist_ok=True)
	seed = bench_execute("crm_cskh.tools.e2e_seed.reset_and_seed")
	users = seed["users"]
	team_a = seed["team_a"]
	team_b = seed["team_b"]

	git_sha = (
		subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode("utf-8").strip()
	)

	sessions = {
		"admin": Session(users["admin"]),
		"bod": Session(users["bod"]),
		"leader_a": Session(users["leader_a"]),
		"leader_b": Session(users["leader_b"]),
		"staff_a": Session(users["staff_a"]),
		"staff_b": Session(users["staff_b"]),
		"outside": Session(users["outside"]),
		"guest": Session(None),
	}
	admin = sessions["admin"]

	evidence = {
		"site": HOST,
		"base_url": BASE,
		"initial_backend_baseline_sha": "3025495f58bc0391c3f8d092af500b0566a5f038",
		"backend_baseline_sha": "64b7486f29ad712106d167dfaea50e3b060a6845",
		"worktree_base_sha": git_sha,
		"checks": [],
	}

	def record(check_id, ok, details):
		if not ok:
			raise AssertionError(f"Check {check_id} FAILED: {details}")
		evidence["checks"].append({"id": check_id, "status": "PASS", "details": redact(details)})
		print(f"PASS  {check_id}", flush=True)

	# ------------------------------------------------------------------
	# 1. Page HTTP gate matrix across all 8 sessions
	# ------------------------------------------------------------------
	expected_pages = {
		"admin": [200, 200, 200, 200, 200, 200],
		"bod": [200, 200, 403, 200, 200, 200],
		"leader_a": [200, 200, 403, 200, 200, 200],
		"leader_b": [200, 200, 403, 200, 200, 200],
		"staff_a": [200, 403, 403, 403, 200, 403],
		"staff_b": [200, 403, 403, 403, 200, 403],
		"outside": [200, 403, 403, 403, 200, 403],
		"guest": [403, 403, 403, 403, 403, 403],
	}
	actual_pages = {}
	for actor_key, sess in sessions.items():
		statuses = []
		for p in PAGES:
			st, raw = sess.raw_get(p)
			statuses.append(st)
			if st == 200:
				html = raw.decode("utf-8", errors="replace")
				assert 'lang="vi"' in html, f"{actor_key} {p} missing lang=vi"
		actual_pages[actor_key] = statuses
		assert statuses == expected_pages[actor_key], (
			f"Page matrix mismatch for {actor_key}: got {statuses}, expected {expected_pages[actor_key]}"
		)
	record("E2E-01-page-role-matrix", True, {"routes": PAGES, "statuses": actual_pages})

	# ------------------------------------------------------------------
	# 2. CSV upload -> mapping -> preview -> confirm -> COMPLETED_WITH_ERRORS + error file download policy
	# ------------------------------------------------------------------
	csv_buf = io.StringIO()
	writer = csv.writer(csv_buf, lineterminator="\n")
	writer.writerow(["so_dt", "ho_ten", "nguon_kh"])
	writer.writerows(
		[
			["0918880101", "<script>window.__xssTriggered=1</script>Khách Thử Nghiệm 01", "Website"],
			["0918880102", "=1+1", "Facebook"],
			["0918880103", "Khách Mẫu 03", "Zalo"],
			["0918880104", "Khách Mẫu 04", "Website"],
			["0918880105", "Khách Mẫu 05", "Sự kiện"],
			["0918880106", "Khách Mẫu 06", "Hotline"],
			["0918880101", "Trùng Trong Tệp", "Website"],
			["9.1888E+08", "Lỗi Ký Hiệu Khoa Học Excel", "Excel"],
			["=SUM(1,2)", "Công Thức Độc Hại", "Excel"],
			["<script>alert(1)</script>", "Thẻ Script Độc Hại", "Web"],
		]
	)
	csv_b64 = base64.b64encode(csv_buf.getvalue().encode("utf-8")).decode("ascii")
	st, up_res = admin.post_api(
		"crm_cskh.api.import_files.upload_import_file",
		{"file_name": "e2e-batch1.csv", "content": csv_b64},
	)
	assert st == 200 and up_res["message"]["is_private"] == 1, up_res

	st, prev_res = admin.post_api(
		"crm_cskh.api.imports.create_preview",
		{
			"file_name": up_res["message"]["name"],
			"mapping": {"phone": "so_dt", "name": "ho_ten", "source": "nguon_kh"},
			"idempotency_key": "e2e-csv-batch-1",
			"import_mode": "standard",
		},
	)
	assert st == 200, prev_res
	csv_preview = prev_res["message"]
	assert csv_preview["status"] == "DRY_RUN"
	assert csv_preview["totals"] == {
		"total_rows": 10,
		"valid_rows": 6,
		"new_rows": 6,
		"duplicate_rows": 1,
		"error_rows": 3,
	}, csv_preview["totals"]

	# Confirm twice to verify anti-double-submit idempotency on confirm
	st1, conf1 = admin.post_api("crm_cskh.api.imports.confirm", {"batch": csv_preview["batch"]})
	st2, conf2 = admin.post_api("crm_cskh.api.imports.confirm", {"batch": csv_preview["batch"]})
	assert st1 == 200 and st2 == 200, (conf1, conf2)

	csv_done = poll_batch(admin, csv_preview["batch"])
	assert csv_done["status"] == "COMPLETED_WITH_ERRORS", csv_done

	st, detail_res = admin.get_api(
		"crm_cskh.api.import_history.batch_detail", {"batch": csv_preview["batch"]}
	)
	assert st == 200, detail_res
	csv_detail = detail_res["message"]
	assert csv_detail["totals"]["created_rows"] == 6, csv_detail
	assert csv_detail["totals"]["error_rows"] == 3, csv_detail
	dl_url = csv_detail["error_artifact"]["download_url"]
	raw_private_url = csv_detail["error_artifact"]["file_url"]

	# Admin downloads error file -> HTTP 200, sanitized formula & stripped <script>
	dl_status, dl_bytes = admin.raw_get(dl_url)
	dl_text = dl_bytes.decode("utf-8")
	assert dl_status == 200 and "row_number,reason" in dl_text, (dl_status, dl_text)
	assert "<script>" not in dl_text, dl_text

	# Non-admin roles + Guest are denied (403) on BOTH download_error_file AND /private/files/...
	denied_downloads = {}
	for actor_key in ("leader_a", "leader_b", "staff_a", "staff_b", "bod", "outside", "guest"):
		s_api, _ = sessions[actor_key].raw_get(dl_url)
		s_raw, _ = sessions[actor_key].raw_get(raw_private_url)
		assert s_api == 403 and s_raw in (401, 403), (actor_key, s_api, s_raw)
		denied_downloads[actor_key] = {"download_endpoint": s_api, "raw_private_url": s_raw}

	record(
		"E2E-02-csv-import-and-error-download-policy",
		True,
		{
			"batch": csv_preview["batch"],
			"status": csv_detail["status"],
			"totals": csv_detail["totals"],
			"admin_download_status": dl_status,
			"admin_download_bytes": len(dl_bytes),
			"denied_actors": denied_downloads,
		},
	)

	# ------------------------------------------------------------------
	# 3. XLSX upload -> preview -> confirm -> COMPLETED + retry failed batch
	# ------------------------------------------------------------------
	xlsx_bytes = build_minimal_xlsx(
		[
			["mobile_no", "ho_ten", "nguon"],
			["0918880201", "Khách XLSX 01", "Hội thảo"],
			["0918880202", "Khách XLSX 02", "Hội thảo"],
			["0918880203", "Khách XLSX 03", "Đối tác"],
			["0918880204", "Khách XLSX 04", "Đối tác"],
			["0918880101", "Khách Trùng CRM", "Website"],
		]
	)
	st, x_up = admin.post_api(
		"crm_cskh.api.import_files.upload_import_file",
		{"file_name": "e2e-batch2.xlsx", "content": base64.b64encode(xlsx_bytes).decode("ascii")},
	)
	assert st == 200, x_up
	st, x_prev = admin.post_api(
		"crm_cskh.api.imports.create_preview",
		{
			"file_name": x_up["message"]["name"],
			"mapping": {"phone": "mobile_no", "name": "ho_ten", "source": "nguon"},
			"idempotency_key": "e2e-xlsx-batch-2",
			"import_mode": "standard",
		},
	)
	assert st == 200, x_prev
	xlsx_batch = x_prev["message"]["batch"]
	assert x_prev["message"]["totals"] == {
		"total_rows": 5,
		"valid_rows": 4,
		"new_rows": 4,
		"duplicate_rows": 1,
		"error_rows": 0,
	}, x_prev

	st, _ = admin.post_api("crm_cskh.api.imports.confirm", {"batch": xlsx_batch})
	assert st == 200
	xlsx_done = poll_batch(admin, xlsx_batch)
	assert xlsx_done["status"] == "COMPLETED", xlsx_done

	# Retry seeded FAILED batch -> transitions FAILED -> QUEUED -> COMPLETED
	st, _ = admin.post_api("crm_cskh.api.imports.confirm", {"batch": seed["failed_batch"]})
	assert st == 200
	retried_done = poll_batch(admin, seed["failed_batch"])
	assert retried_done["status"] == "COMPLETED", retried_done

	record(
		"E2E-03-xlsx-import-and-failed-batch-retry",
		True,
		{
			"xlsx_batch": xlsx_batch,
			"xlsx_status": xlsx_done["status"],
			"xlsx_totals": xlsx_done["totals"],
			"retried_failed_batch": seed["failed_batch"],
			"retried_status": retried_done["status"],
		},
	)

	# ------------------------------------------------------------------
	# 4. Pool before distribution + Campaign/Rules + Stale Preview + Execute + Double-submit
	# ------------------------------------------------------------------
	st, pool_before = admin.get_api("crm_cskh.api.pool_ui.list", {"page": 1, "page_size": 50})
	assert st == 200, pool_before
	states_before = {}
	for row in pool_before["message"]["items"]:
		states_before[row["state"]] = states_before.get(row["state"], 0) + 1
	assert states_before.get("eligible") == 11, states_before
	assert states_before.get("conflict") == 1, states_before
	assert states_before.get("invalid") == 1, states_before

	# Create Team A campaign, Team B campaign, and Draft->Closed campaign
	st, c_a = admin.post_api(
		"crm_cskh.api.campaigns.save",
		{
			"code": "CAMP-TEAM-A",
			"title": "Chiến dịch Chăm sóc Đội A",
			"description": "Phân bổ trọng số 60/40 cho Đội A",
			"status": "Active",
			"team_scope": team_a,
		},
	)
	assert st == 200, c_a
	camp_a = c_a["message"]["campaign"]

	# Import 2 Team B leads & distribute CAMP-TEAM-B first so its 2 active claims
	# are genuinely excluded (`active_claim_in_other_campaign`) when CAMP-TEAM-A previews all pool leads!
	csv_b = "so_dt,ho_ten,nguon_kh\n0918880301,Khách Đội B 01,Đối tác\n0918880302,Khách Đội B 02,Đối tác\n"
	_, up_b = admin.post_api(
		"crm_cskh.api.import_files.upload_import_file",
		{"file_name": "e2e-team-b.csv", "content": base64.b64encode(csv_b.encode("utf-8")).decode("ascii")},
	)
	_, prev_b = admin.post_api(
		"crm_cskh.api.imports.create_preview",
		{
			"file_name": up_b["message"]["name"],
			"mapping": {"phone": "so_dt", "name": "ho_ten", "source": "nguon_kh"},
			"idempotency_key": "e2e-team-b-csv",
		},
	)
	admin.post_api("crm_cskh.api.imports.confirm", {"batch": prev_b["message"]["batch"]})
	poll_batch(admin, prev_b["message"]["batch"])

	_, pool_b_search = admin.get_api("crm_cskh.api.pool_ui.list", {"search": "091888030", "page_size": 20})
	team_b_leads = [r["lead"] for r in pool_b_search["message"]["items"]]
	assert len(team_b_leads) == 2, pool_b_search

	st, c_b = admin.post_api(
		"crm_cskh.api.campaigns.save",
		{
			"code": "CAMP-TEAM-B",
			"title": "Chiến dịch Chăm sóc Đội B",
			"description": "Phân bổ riêng cho Đội B",
			"status": "Active",
			"team_scope": team_b,
			"selection": {"leads": team_b_leads},
		},
	)
	assert st == 200, c_b
	camp_b = c_b["message"]["campaign"]

	admin.post_api(
		"crm_cskh.api.campaigns.save_rule",
		{
			"campaign": camp_b,
			"user": users["staff_c"],
			"weight": 100,
			"capacity_limit": 0,
			"team": team_b,
			"enabled": 1,
		},
	)
	_, dist_prev_b = admin.post_api(
		"crm_cskh.api.distribution.preview",
		{"campaign": camp_b, "idempotency_key": "e2e-prev-fresh-b"},
	)
	_, dist_exec_b = admin.post_api(
		"crm_cskh.api.distribution.execute",
		{
			"preview_id": dist_prev_b["message"]["preview_id"],
			"expected_rule_version": dist_prev_b["message"]["rule_version"],
			"idempotency_key": "e2e-exec-fresh-b",
		},
	)
	assert dist_exec_b["message"]["created"] == 2, dist_exec_b

	st, c_closed = admin.post_api(
		"crm_cskh.api.campaigns.save",
		{
			"code": "CAMP-CLOSED-01",
			"title": "Chiến dịch Đã Kết Thúc",
			"status": "Draft",
			"team_scope": team_a,
		},
	)
	assert st == 200, c_closed
	st, c_closed2 = admin.post_api(
		"crm_cskh.api.campaigns.save",
		{
			"campaign": c_closed["message"]["campaign"],
			"code": "CAMP-CLOSED-01",
			"title": "Chiến dịch Đã Kết Thúc",
			"status": "Closed",
			"team_scope": team_a,
		},
	)
	assert st == 200 and c_closed2["message"]["status"] == "Closed", c_closed2

	# Save rules for Team A: staff_a weight=60 capacity=0 (Unlimited), staff_b weight=40 capacity=5
	st, r1 = admin.post_api(
		"crm_cskh.api.campaigns.save_rule",
		{
			"campaign": camp_a,
			"user": users["staff_a"],
			"weight": 60,
			"capacity_limit": 0,
			"team": team_a,
			"enabled": 1,
		},
	)
	st2, r2 = admin.post_api(
		"crm_cskh.api.campaigns.save_rule",
		{
			"campaign": camp_a,
			"user": users["staff_b"],
			"weight": 40,
			"capacity_limit": 5,
			"team": team_a,
			"enabled": 1,
		},
	)
	assert st == 200 and st2 == 200, (r1, r2)

	# Stale preview #1: RULE_CHANGED (409)
	st, stale_prev = admin.post_api(
		"crm_cskh.api.distribution.preview",
		{"campaign": camp_a, "idempotency_key": "e2e-prev-stale-rule"},
	)
	assert st == 200, stale_prev
	old_version = stale_prev["message"]["rule_version"]

	# Bump rule version (`version = old_version + 1` creates new epoch)
	st, bump_a = admin.post_api(
		"crm_cskh.api.campaigns.save_rule",
		{
			"campaign": camp_a,
			"user": users["staff_a"],
			"weight": 60,
			"capacity_limit": 0,
			"team": team_a,
			"enabled": 1,
			"version": old_version + 1,
		},
	)
	st2, bump_b = admin.post_api(
		"crm_cskh.api.campaigns.save_rule",
		{
			"campaign": camp_a,
			"user": users["staff_b"],
			"weight": 40,
			"capacity_limit": 10,
			"team": team_a,
			"enabled": 1,
			"version": old_version + 1,
		},
	)
	assert st == 200 and st2 == 200, (bump_a, bump_b)

	st_stale, body_stale = admin.post_api(
		"crm_cskh.api.distribution.execute",
		{
			"preview_id": stale_prev["message"]["preview_id"],
			"expected_rule_version": old_version,
			"idempotency_key": "e2e-exec-stale-rule",
		},
	)
	assert st_stale == 409 and body_stale.get("p1_code") == "RULE_CHANGED", (st_stale, body_stale)

	# Stale preview #2: PREVIEW_EXPIRED (409)
	st, exp_prev = admin.post_api(
		"crm_cskh.api.distribution.preview",
		{"campaign": camp_a, "idempotency_key": "e2e-prev-expired"},
	)
	assert st == 200, exp_prev
	bench_execute(
		"crm_cskh.tools.e2e_seed.expire_preview",
		{"preview_id": exp_prev["message"]["preview_id"]},
	)
	st_exp, body_exp = admin.post_api(
		"crm_cskh.api.distribution.execute",
		{
			"preview_id": exp_prev["message"]["preview_id"],
			"expected_rule_version": exp_prev["message"]["rule_version"],
			"idempotency_key": "e2e-exec-expired",
		},
	)
	assert st_exp == 409 and body_exp.get("p1_code") == "PREVIEW_EXPIRED", (st_exp, body_exp)

	# Fresh preview -> execute + double-submit idempotency
	st, fresh_prev = admin.post_api(
		"crm_cskh.api.distribution.preview",
		{"campaign": camp_a, "idempotency_key": "e2e-prev-fresh-a"},
	)
	assert st == 200, fresh_prev
	fp = fresh_prev["message"]
	assert fp["summary"]["eligible_count"] == 11, fp
	assert fp["summary"]["excluded_count"] >= 1, fp  # conflict_lead (`owned_outside_cskh`)

	st_ex1, ex1 = admin.post_api(
		"crm_cskh.api.distribution.execute",
		{
			"preview_id": fp["preview_id"],
			"expected_rule_version": fp["rule_version"],
			"idempotency_key": "e2e-exec-fresh-a",
		},
	)
	st_ex2, ex2 = admin.post_api(
		"crm_cskh.api.distribution.execute",
		{
			"preview_id": fp["preview_id"],
			"expected_rule_version": fp["rule_version"],
			"idempotency_key": "e2e-exec-fresh-a",
		},
	)
	assert st_ex1 == 200 and st_ex2 == 200, (ex1, ex2)
	run_a = ex1["message"]
	assert run_a["status"] == "completed" and run_a["created"] == 11, run_a
	assert run_a["assigned"][users["staff_a"]] == 7, run_a
	assert run_a["assigned"][users["staff_b"]] == 4, run_a

	# Verify exact delivery to Staff A and Staff B `my_list`
	_, my_a = sessions["staff_a"].get_api("crm_cskh.api.customers.my_list", {"page": 1, "page_size": 50})
	_, my_b = sessions["staff_b"].get_api("crm_cskh.api.customers.my_list", {"page": 1, "page_size": 50})
	leads_a = {item["lead"] for item in my_a["message"]["items"]}
	leads_b = {item["lead"] for item in my_b["message"]["items"]}
	assert my_a["message"]["total"] == 7 and len(leads_a) == 7, my_a
	assert my_b["message"]["total"] == 4 and len(leads_b) == 4, my_b
	assert not (leads_a & leads_b), "Cross-leak between Staff A and Staff B!"

	record(
		"E2E-04-campaign-rules-stale-preview-and-execute",
		True,
		{
			"campaign_a": camp_a,
			"stale_rule_changed": {"http": st_stale, "code": body_stale.get("p1_code")},
			"stale_preview_expired": {"http": st_exp, "code": body_exp.get("p1_code")},
			"run_id": run_a["run_id"],
			"assigned": run_a["assigned"],
			"staff_a_my_list_total": my_a["message"]["total"],
			"staff_b_my_list_total": my_b["message"]["total"],
		},
	)

	# ------------------------------------------------------------------
	# 5. Reassign & Revoke + Stale expected_owner CONFLICT + Live vs Audit Event Reconciliation
	# ------------------------------------------------------------------
	_, dash_pre = admin.get_api("crm_cskh.api.dashboard.distribution", {"campaign": camp_a})
	assert dash_pre["message"]["cards"]["assigned_work_items"] == 11
	assert dash_pre["message"]["assignment_events"] == 11

	moving_item = my_a["message"]["items"][0]
	moving_wi = moving_item["work_item"]
	moving_lead = moving_item["lead"]

	# Before reassign: Staff A reads CRM Lead (200), Staff B gets 403
	st_lead_a_before, _ = sessions["staff_a"].raw_get(f"/api/resource/CRM%20Lead/{moving_lead}")
	st_lead_b_before, _ = sessions["staff_b"].raw_get(f"/api/resource/CRM%20Lead/{moving_lead}")
	assert st_lead_a_before == 200 and st_lead_b_before == 403, (st_lead_a_before, st_lead_b_before)

	# Reassign Staff A -> Staff B (+ idempotent retry with same request_id)
	st_re1, re1 = admin.post_api(
		"crm_cskh.api.ownership.reassign",
		{
			"work_item": moving_wi,
			"expected_owner": users["staff_a"],
			"target_user": users["staff_b"],
			"reason": "Điều phối tải ca sáng giữa A1 và A2",
			"request_id": "e2e-reassign-req-1",
		},
	)
	st_re2, re2 = admin.post_api(
		"crm_cskh.api.ownership.reassign",
		{
			"work_item": moving_wi,
			"expected_owner": users["staff_a"],
			"target_user": users["staff_b"],
			"reason": "Điều phối tải ca sáng giữa A1 và A2",
			"request_id": "e2e-reassign-req-1",
		},
	)
	assert st_re1 == 200 and st_re2 == 200, (re1, re2)
	assert re1["message"]["status"] == "reassigned" and re2["message"]["status"] == "already_applied"

	# After reassign: Staff A immediately loses access (403), Staff B gains access (200)
	st_lead_a_after, _ = sessions["staff_a"].raw_get(f"/api/resource/CRM%20Lead/{moving_lead}")
	st_lead_b_after, _ = sessions["staff_b"].raw_get(f"/api/resource/CRM%20Lead/{moving_lead}")
	assert st_lead_a_after == 403 and st_lead_b_after == 200, (st_lead_a_after, st_lead_b_after)

	# Stale expected_owner -> 409 CONFLICT
	st_conf, body_conf = admin.post_api(
		"crm_cskh.api.ownership.reassign",
		{
			"work_item": moving_wi,
			"expected_owner": users["staff_a"],
			"target_user": users["staff_a"],
			"reason": "Thử ghi đè bằng owner cũ",
			"request_id": "e2e-reassign-stale-owner",
		},
	)
	assert st_conf == 409 and body_conf.get("p1_code") == "CONFLICT", (st_conf, body_conf)

	# Dashboard reconciliation: live assigned_work_items stays 11, assignment_events becomes 12!
	_, dash_mid = admin.get_api("crm_cskh.api.dashboard.distribution", {"campaign": camp_a})
	assert dash_mid["message"]["cards"]["assigned_work_items"] == 11, dash_mid
	assert dash_mid["message"]["assignment_events"] == 12, dash_mid

	# Ownership history has append-only [assign, reassign]
	st_hist, hist_res = admin.get_api("crm_cskh.api.ownership.history", {"work_item": moving_wi})
	assert st_hist == 200, hist_res
	actions = [ev["action"] for ev in hist_res["message"]["items"]]
	assert actions == ["assign", "reassign"], actions

	# Revoke 1 item from Staff B (a different item so `moving_wi` keeps `[assign, reassign]` visible!)
	_, my_b_mid = sessions["staff_b"].get_api("crm_cskh.api.customers.my_list", {"page": 1, "page_size": 50})
	other_b = [it for it in my_b_mid["message"]["items"] if it["work_item"] != moving_wi][0]
	st_rev, rev_res = admin.post_api(
		"crm_cskh.api.ownership.revoke",
		{
			"work_item": other_b["work_item"],
			"expected_owner": users["staff_b"],
			"reason": "Khách hẹn gọi lại tuần tới, đưa về hàng đợi",
			"request_id": "e2e-revoke-req-1",
		},
	)
	assert st_rev == 200, rev_res
	st_lead_b_revoked, _ = sessions["staff_b"].raw_get(f"/api/resource/CRM%20Lead/{other_b['lead']}")
	assert st_lead_b_revoked == 403, st_lead_b_revoked

	record(
		"E2E-05-reassign-revoke-conflict-and-reconciliation",
		True,
		{
			"reassigned_work_item": moving_wi,
			"staff_a_lead_http_before_after": [st_lead_a_before, st_lead_a_after],
			"staff_b_lead_http_before_after": [st_lead_b_before, st_lead_b_after],
			"stale_owner_conflict": {"http": st_conf, "code": body_conf.get("p1_code")},
			"live_assigned_after_transfer": dash_mid["message"]["cards"]["assigned_work_items"],
			"audit_events_after_transfer": dash_mid["message"]["assignment_events"],
			"revoked_work_item": other_b["work_item"],
			"staff_b_lead_http_after_revoke": st_lead_b_revoked,
		},
	)

	# ------------------------------------------------------------------
	# 6. Multi-Role Isolation, BOD PII Omission, CSRF & 404
	# ------------------------------------------------------------------
	# Seed 1 FAILED batch and 1 failed run on CAMP-TEAM-A so UI displays failed/retry states too
	failed_demo = bench_execute(
		"crm_cskh.tools.e2e_seed.seed_failed_states_for_ui", {"campaign": camp_a}
	)

	# Leader A vs Leader B strict isolation
	_, la_dash = sessions["leader_a"].get_api("crm_cskh.api.dashboard.distribution")
	_, lb_dash = sessions["leader_b"].get_api("crm_cskh.api.dashboard.distribution")
	la_users = {r["user"]: r["current_total"] for r in la_dash["message"]["per_user"]}
	lb_users = {r["user"]: r["current_total"] for r in lb_dash["message"]["per_user"]}
	assert la_users == {users["staff_a"]: 6, users["staff_b"]: 4}, la_users
	assert lb_users == {users["staff_c"]: 2}, lb_users

	_, la_assigned = sessions["leader_a"].get_api("crm_cskh.api.ownership_ui.assigned_list", {"page_size": 50})
	_, lb_assigned = sessions["leader_b"].get_api("crm_cskh.api.ownership_ui.assigned_list", {"page_size": 50})
	assert all(it["assigned_to"] in (users["staff_a"], users["staff_b"]) for it in la_assigned["message"]["items"])
	assert all(it["assigned_to"] == users["staff_c"] for it in lb_assigned["message"]["items"])

	# BOD aggregate read + ZERO PII + search forbidden + mutations forbidden
	_, bod_dash = sessions["bod"].get_api("crm_cskh.api.dashboard.distribution")
	assert bod_dash["message"]["cards"]["assigned_work_items"] == 12, bod_dash
	_, bod_pool = sessions["bod"].get_api("crm_cskh.api.pool_ui.list", {"page_size": 50})
	assert len(bod_pool["message"]["items"]) >= 12
	assert all(it["phone"] is None and it["display_name"] == "" for it in bod_pool["message"]["items"])
	st_bod_search, _ = sessions["bod"].get_api("crm_cskh.api.pool_ui.list", {"search": "0918"})
	assert st_bod_search == 403, st_bod_search

	# Non-admin mutations forbidden (403)
	for non_admin in ("leader_a", "leader_b", "staff_a", "bod"):
		s1, b1 = sessions[non_admin].post_api(
			"crm_cskh.api.campaigns.save",
			{"code": f"FORBID-{non_admin}", "title": "Forbidden", "status": "Draft"},
		)
		s2, b2 = sessions[non_admin].post_api(
			"crm_cskh.api.ownership.revoke",
			{
				"work_item": moving_wi,
				"expected_owner": users["staff_b"],
				"reason": "Trái quyền",
				"request_id": f"forbid-{non_admin}",
			},
		)
		assert s1 == 403 and b1.get("p1_code") == "FORBIDDEN", (non_admin, s1, b1)
		assert s2 == 403 and b2.get("p1_code") == "FORBIDDEN", (non_admin, s2, b2)

	# Missing/invalid CSRF on POST -> rejected (400 CSRFTokenError)
	st_csrf, body_csrf = admin.post_api(
		"crm_cskh.api.campaigns.save",
		{"code": "CSRF-BAD", "title": "Bad CSRF", "status": "Draft"},
		csrf_override="invalid-csrf-token",
	)
	assert st_csrf in (400, 403) and body_csrf.get("exc_type") == "CSRFTokenError", (st_csrf, body_csrf)

	# Non-existent run lookup -> 404 (DoesNotExistError / NOT_FOUND) + non-existent error file -> 404 NOT_FOUND
	st_404, body_404 = admin.get_api("crm_cskh.api.distribution.get_run", {"run_id": "RUN-DOES-NOT-EXIST"})
	assert st_404 == 404 and (
		body_404.get("p1_code") == "NOT_FOUND" or body_404.get("exc_type") == "DoesNotExistError"
	), (st_404, body_404)
	st_404_err, body_404_err = admin.get_api(
		"crm_cskh.api.import_history.download_error_file", {"batch": "BATCH-DOES-NOT-EXIST"}
	)
	assert st_404_err == 404 and body_404_err.get("p1_code") == "NOT_FOUND", (st_404_err, body_404_err)

	record(
		"E2E-06-team-isolation-bod-pii-csrf-and-404",
		True,
		{
			"campaign_b": camp_b,
			"leader_a_per_user": la_users,
			"leader_b_per_user": lb_users,
			"bod_assigned_total": bod_dash["message"]["cards"]["assigned_work_items"],
			"bod_pool_pii_leaked": 0,
			"bod_search_http": st_bod_search,
			"csrf_invalid_http": st_csrf,
			"run_not_found_http": st_404,
			"failed_ui_demo": failed_demo,
		},
	)

	evidence["final_ids"] = {
		"campaign_a": camp_a,
		"campaign_b": camp_b,
		"csv_batch": csv_preview["batch"],
		"xlsx_batch": xlsx_batch,
		"run_a": run_a["run_id"],
		"reassigned_work_item": moving_wi,
		"failed_batch_demo": failed_demo["failed_batch"],
		"failed_run_demo": failed_demo["failed_run"],
	}

	with open(REPORT_PATH, "w", encoding="utf-8", newline="\n") as fh:
		json.dump(redact(evidence), fh, ensure_ascii=False, indent=2)
		fh.write("\n")

	print(f"ALL HTTP E2E CHECKS PASSED -> {REPORT_PATH}", flush=True)
	return 0


if __name__ == "__main__":
	sys.exit(main())
