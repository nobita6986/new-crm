#!/usr/bin/env python3
"""P1 T1B — race thật bằng N PROCESS RIÊNG (không phải thread).

P1 §12.4 đòi race import và race distribution bằng **process riêng**, không
phải thread trong một process: chỉ khi đó mới chứng minh được unique index của
MariaDB là trọng tài thật, chứ không phải GIL của Python.

Kịch bản:

* **import race**: N process cùng tạo một Lead cho cùng số điện thoại và cùng
  claim canonical identity. Kết quả: đúng **1** dòng `CSKH Identity Index` và
  đúng **1** Lead cho số điện thoại đó.
* **distribution race**: N process cùng tạo một Work Item cho cùng lead và cùng
  claim `CSKH Active Claim`. Kết quả: đúng **1** claim và đúng **1** work item
  cho (campaign, lead).
* **query hậu kiểm**: `count(*) group by lead having count(*) > 1` = 0.

Cách dùng (trong container bench):

    bench --site p1fresh.localhost execute crm_cskh.tools.p1_process_race.run \
        --kwargs "{'mode': 'import', 'workers': 5}"

Exit code khác 0 ⇒ race thất bại (fail-closed).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import frappe

from crm_cskh.services.phone_identity import normalize_vn_phone


def _child_site():
	return getattr(frappe.local, "site", None)


def _child_sites_path():
	return getattr(frappe.local, "sites_path", None)


def _child_work(kind, payload):
	"""Do the racing write in a **child process** with its own connection."""
	import frappe as child_frappe

	from crm_cskh.services import identity as identity_service

	site = payload["site"]
	sites_path = payload["sites_path"]
	child_frappe.init(site, sites_path=sites_path, force=True)
	child_frappe.connect()
	child_frappe.set_user("Administrator")
	try:
		if kind == "import":
			phone = payload["phone"]
			canonical = normalize_vn_phone(phone)
			lead = child_frappe.get_doc(
				{
					"doctype": "CRM Lead",
					"first_name": "Race",
					"mobile_no": phone,
					"status": "New",
				}
			)
			lead.insert(ignore_permissions=True)
			try:
				winner = identity_service.claim_canonical_identity(canonical, lead.name, allow_adopt=False)
			except Exception:
				winner = identity_service.find_by_phone(canonical)
				if not winner:
					raise
			if winner != lead.name:
				child_frappe.delete_doc("CRM Lead", lead.name, force=True, ignore_permissions=True)
			child_frappe.db.commit()
			return {"ok": True, "winner": winner}
		# distribution race
		lead = payload["lead"]
		campaign = payload["campaign"]
		work_item = child_frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"campaign": campaign,
				"lead": lead,
				"status": "queued",
			}
		)
		work_item.insert(ignore_permissions=True)
		claim = child_frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead,
				"campaign": campaign,
				"work_item": work_item.name,
			}
		)
		claim.insert(ignore_permissions=True)
		child_frappe.db.commit()
		return {"ok": True, "work_item": work_item.name}
	except Exception as exc:
		try:
			child_frappe.db.rollback()
		except Exception:
			pass
		return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
	finally:
		try:
			child_frappe.db.close()
		except Exception:
			pass


def _run_child(kind, payload):
	"""Spawn a child process and return its JSON result."""
	with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
		payload_path = handle.name
	result_path = payload_path + ".result"
	payload = dict(payload)
	payload["kind"] = kind
	payload["result_path"] = result_path
	with open(payload_path, "w", encoding="utf-8") as handle:
		json.dump(payload, handle)
	try:
		completed = subprocess.run(
			[sys.executable, "-c", CHILD_ENTRY],
			capture_output=True,
			text=True,
			timeout=300,
			env={**os.environ, "CSKH_RACE_PAYLOAD": payload_path},
		)
		if completed.returncode != 0:
			return {
				"ok": False,
				"error": f"child exited {completed.returncode}: {completed.stderr[-800:]}",
			}
		with open(result_path, encoding="utf-8") as handle:
			return json.load(handle)
	except subprocess.TimeoutExpired:
		return {"ok": False, "error": "child timed out"}
	except (OSError, ValueError) as exc:
		return {"ok": False, "error": f"child result unreadable: {exc}"}
	finally:
		for path in (payload_path, result_path):
			try:
				os.unlink(path)
			except OSError:
				pass


CHILD_ENTRY = """
import json
import os
import sys

sys.path.insert(0, os.getcwd())

import frappe

from crm_cskh.tools import p1_process_race

with open(os.environ["CSKH_RACE_PAYLOAD"], encoding="utf-8") as handle:
    payload = json.load(handle)

kind = payload.pop("kind")
result_path = payload.pop("result_path")
result = p1_process_race._child_work(kind, payload)
with open(result_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle)
"""


def run(mode="import", workers=5):
	"""Run the race and print a JSON verdict. Raises on failure."""
	workers = int(workers)
	site = _child_site()
	sites_path = _child_sites_path()

	# The parent reads the result with its own connection. Under the default
	# REPEATABLE READ isolation the snapshot of the parent transaction is
	# frozen at the first read, so the parent must COMMIT **before** the race
	# (to release its snapshot) and **after** it (to read the rows the children
	# committed). Without the commits the parent would count 0 rows although
	# the children really wrote them.
	frappe.db.commit()

	base = {"site": site, "sites_path": sites_path}

	if mode == "import":
		phone = _unique_phone()
		payloads = [dict(base, kind="import", phone=phone) for _ in range(workers)]
		results = [_run_child("import", payload) for payload in payloads]
		frappe.db.commit()
		canonical = normalize_vn_phone(phone)
		identity_rows = frappe.db.count("CSKH Identity Index", {"normalized_phone": canonical})
		lead_rows = frappe.db.count("CRM Lead", {"mobile_no": ["in", (phone, "+84" + phone[1:])]})
		duplicate_leads = frappe.db.sql(
			"""select mobile_no, count(*) as c from `tabCRM Lead`
			   where mobile_no in (%s, %s) group by mobile_no having count(*) > 1""",
			(phone, "+84" + phone[1:]),
		)
		ok = identity_rows == 1 and lead_rows == 1 and not duplicate_leads
		verdict = {
			"mode": "import",
			"workers": workers,
			"phone": phone,
			"canonical": canonical,
			"identity_rows": identity_rows,
			"lead_rows": lead_rows,
			"duplicate_leads": len(duplicate_leads),
			"winners": sum(1 for r in results if r.get("ok")),
			"errors": [r.get("error") for r in results if not r.get("ok")],
			"ok": ok,
		}
	else:
		lead = _make_lead()
		campaign = _make_campaign()
		payloads = [dict(base, kind="distribution", lead=lead, campaign=campaign) for _ in range(workers)]
		results = [_run_child("distribution", payload) for payload in payloads]
		frappe.db.commit()
		claims = frappe.db.count("CSKH Active Claim", {"lead": lead})
		items = frappe.db.count("CSKH Work Item", {"campaign": campaign, "lead": lead})
		duplicate_items = frappe.db.sql(
			"""select campaign, lead, count(*) as c from `tabCSKH Work Item`
			   where campaign = %s and lead = %s
			   group by campaign, lead having count(*) > 1""",
			(campaign, lead),
		)
		ok = claims == 1 and items == 1 and not duplicate_items
		verdict = {
			"mode": "distribution",
			"workers": workers,
			"lead": lead,
			"campaign": campaign,
			"claims": claims,
			"work_items": items,
			"duplicate_items": len(duplicate_items),
			"winners": sum(1 for r in results if r.get("ok")),
			"errors": [r.get("error") for r in results if not r.get("ok")],
			"ok": ok,
		}

	print(json.dumps(verdict, indent=2, ensure_ascii=False))
	if not verdict["ok"]:
		raise RuntimeError(f"P1 process race failed: {verdict}")
	return verdict


def _unique_phone():
	digits = f"{int(frappe.generate_hash(length=10), 16) % 100000000:08d}"
	phone = f"09{digits}"
	while frappe.db.exists("CRM Lead", {"mobile_no": phone}):
		digits = f"{int(frappe.generate_hash(length=10), 16) % 100000000:08d}"
		phone = f"09{digits}"
	return phone


def _make_lead():
	from crm_cskh.services import identity as identity_service

	phone = _unique_phone()
	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": "Race",
			"mobile_no": phone,
			"status": "New",
		}
	)
	lead.insert(ignore_permissions=True)
	identity_service.claim_canonical_identity(normalize_vn_phone(phone), lead.name, allow_adopt=False)
	frappe.db.commit()
	return lead.name


def _make_campaign():
	campaign = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": f"RACE-{frappe.generate_hash(length=8)}",
			"title": "Race campaign",
			"status": "Active",
		}
	)
	campaign.insert(ignore_permissions=True)
	frappe.db.commit()
	return campaign.name
