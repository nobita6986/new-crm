#!/usr/bin/env python3
"""Guarded Synthetic Dataset Seeder for Provisioned Non-Production Staging Sites (`P4-03`/`P4-08`).

Because `crm_cskh.tools.p3_e2e_seed` inside the locked image (`4de3dc8`) restricts
`ALLOWED_P3_SITES` to `p3-*.localhost`, provisioning a real Staging FQDN (e.g.
`staging-crm.<domain>`) requires an explicit, multi-guard operator CLI wrapper so
synthetic personas and the 9 `P4.md §5.6` test scenarios can be seeded without
modifying `apps/**` or rebuilding the immutable image.

Safety guards (hard fail before touching Frappe):
  1. `COMPOSE_PROJECT_NAME` MUST contain `staging` and MUST NOT contain `prod`.
  2. `SITE_NAME` MUST NOT match production naming (`prod.*`, `crm.<root>` without `staging`).
  3. `--confirm-staging-site` MUST equal `SITE_NAME` byte-for-byte.
  4. Inside MariaDB, refuses to run if ANY `CRM Lead` row exists whose `email` is not
     blank / `@example.test` / `@example.invalid` (prevents accidental wipe of real PII).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

OPS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OPS_DIR))

import staging_https_smoke_and_reconcile as p403_tool  # noqa: E402


FORBIDDEN_OWNER_PROJECTS = {
    "crm-cskh-vps-staging",
    "crm-cskh-prod",
    "crm-cskh-dev",
    "crm-cskh-p1",
    "crm-cskh-p2",
    "crm-cskh-p3-ui",
}
FORBIDDEN_OWNER_SITES = {
    "crm-staging.localhost",
    "crm.hrpartner.vn",
    "dev.localhost",
    "p3ui.localhost",
}


def validate_staging_seed_target(env: dict[str, str], confirmed_site: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    proj = env.get("COMPOSE_PROJECT_NAME", "").strip().lower()
    site = env.get("SITE_NAME", "").strip()

    if proj in FORBIDDEN_OWNER_PROJECTS:
        errors.append(
            f"Refusing seed: COMPOSE_PROJECT_NAME={proj!r} is a protected Owner live/dev stack."
        )
    if site.lower() in FORBIDDEN_OWNER_SITES:
        errors.append(
            f"Refusing seed: SITE_NAME={site!r} is the protected Owner live/dev site."
        )
    if "prod" in proj or "staging" not in proj:
        errors.append(
            f"Refusing seed: COMPOSE_PROJECT_NAME={proj!r} must contain 'staging' and never 'prod'."
        )
    if not site or "prod" in site.lower() or "staging" not in site.lower():
        errors.append(
            f"Refusing seed: SITE_NAME={site!r} must explicitly contain 'staging' and never 'prod'."
        )
    if confirmed_site != site:
        errors.append(
            f"Refusing seed: --confirm-staging-site={confirmed_site!r} does not match SITE_NAME={site!r}."
        )
    return len(errors) == 0, errors


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--confirm-staging-site", required=True)
    parser.add_argument("--mode", choices=("tls", "isolated-smoke", "dr-https"), default="tls")
    parser.add_argument("--seed-password-env", default="CSKH_STAGING_SEED_PASSWORD")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    env = p403_tool.parse_env_file(args.env_file)
    ok, errors = validate_staging_seed_target(env, args.confirm_staging_site)
    if not ok:
        payload: dict[str, Any] = {"status": "REFUSED", "errors": errors}
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return 2

    seed_pw = os.environ.get(args.seed_password_env, "") or ("Stg-" + secrets.token_urlsafe(18) + "!")
    site = env["SITE_NAME"].strip()

    py_code = r"""
import json, os, sys, frappe
payload = json.loads(sys.stdin.read())
site = payload["site"]
os.environ["P3_SEED_PASSWORD"] = payload["seed_pw"]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    non_synth = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCRM Lead` WHERE IFNULL(email,'') != '' "
        "AND email NOT LIKE '%@example.test' AND email NOT LIKE '%@example.invalid'"
    )[0][0]
    if int(non_synth or 0) > 0:
        raise RuntimeError(f"Refusing seed: found {non_synth} non-synthetic CRM Lead rows on {site}")
    import crm_cskh.tools.p3_e2e_seed as seed_mod
    seed_mod.ALLOWED_P3_SITES.add(site)
    info = seed_mod.reset_and_seed_p3()
    frappe.db.commit()
    print("STAGING_SEED_JSON=" + json.dumps(info))
finally:
    frappe.destroy()
"""
    proc = subprocess.run(
        p403_tool._compose_cmd(args.env_file, mode=args.mode)
        + ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", py_code],
        input=json.dumps({"site": site, "seed_pw": seed_pw}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("STAGING_SEED_JSON="):
            res = {"status": "SEEDED", "seed_summary": json.loads(line.split("=", 1)[1])}
            out_txt = p403_tool.redact_sensitive(json.dumps(res, indent=2, ensure_ascii=False), [seed_pw]) + "\n"
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(out_txt, encoding="utf-8", newline="\n")
            sys.stdout.write(out_txt)
            return 0
    raise RuntimeError("Staging seed helper did not emit STAGING_SEED_JSON")


if __name__ == "__main__":
    raise SystemExit(main())
