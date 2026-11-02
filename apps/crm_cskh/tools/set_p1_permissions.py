"""One-off helper: rewrite the role permissions of the P1 DocTypes.

The P1 DocTypes are **read-only for the business roles**: every mutation goes
through the controlled services (`crm_cskh.services.*`), never through the
generic Frappe document endpoints (P1 §8.2: *"Không cho Staff mutate
`lead_owner`, `ToDo`, `DocShare`/Work Item bằng generic Frappe endpoints"*).

This is a deliberate strengthening of ADR-P1-07 §3.4, which listed
`read/write` for the CRM Admin on `CSKH Campaign` / `CSKH Assignment Rule` /
`CSKH Work Item` / `CSKH Import Batch` / `CSKH Distribution Run`: a generic
`write` on a work item would let a caller change `assigned_to` without the
ownership service, breaking the "one current owner" invariant.

Run from the app root:

    python tools/set_p1_permissions.py
"""

import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parents[1] / "crm_cskh" / "crm_cskh" / "doctype"

READ_ONLY = {"read": 1, "create": 0, "write": 0, "delete": 0, "share": 0, "export": 0, "email": 0, "print": 0}
SYSTEM_MANAGER = {
	"role": "System Manager",
	"read": 1,
	"create": 1,
	"write": 1,
	"delete": 0,
	"share": 0,
	"export": 0,
	"email": 0,
	"print": 0,
}


def perm(role, template):
	row = {"role": role}
	row.update(template)
	return row


# Role-level `read` grants only. The `has_permission` hook in
# `crm_cskh.p1_permissions` denies every mutation for a non-system actor.
PLAN = {
	"cskh_campaign": [
		perm("CSKH CRM Admin", READ_ONLY),
		perm("CSKH BOD Viewer", READ_ONLY),
		perm("Sales Manager", READ_ONLY),
	],
	"cskh_assignment_rule": [
		perm("CSKH CRM Admin", READ_ONLY),
		perm("CSKH BOD Viewer", READ_ONLY),
		perm("Sales Manager", READ_ONLY),
	],
	"cskh_work_item": [
		perm("CSKH CRM Admin", READ_ONLY),
		perm("CSKH BOD Viewer", READ_ONLY),
		perm("Sales Manager", READ_ONLY),
		perm("Sales User", READ_ONLY),
	],
	"cskh_assignment_history": [
		perm("CSKH CRM Admin", READ_ONLY),
		perm("CSKH BOD Viewer", READ_ONLY),
		perm("Sales Manager", READ_ONLY),
		perm("Sales User", READ_ONLY),
	],
	"cskh_import_batch": [perm("CSKH CRM Admin", READ_ONLY)],
	"cskh_distribution_run": [
		perm("CSKH CRM Admin", READ_ONLY),
		perm("CSKH BOD Viewer", READ_ONLY),
		perm("Sales Manager", READ_ONLY),
	],
	"cskh_identity_index": [],
	"cskh_active_claim": [],
}


def main():
	for folder, roles in PLAN.items():
		path = BASE / folder / f"{folder}.json"
		data = json.loads(path.read_text(encoding="utf-8"))
		permissions = [dict(SYSTEM_MANAGER)]
		permissions.extend(roles)
		data["permissions"] = permissions
		path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
		print(f"updated {path}")


if __name__ == "__main__":
	main()
