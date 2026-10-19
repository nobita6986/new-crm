# P0 isolated runtime verification

**Date:** 2026-10-15
**Environment:** Docker Desktop/WSL2; pinned Frappe `15.123.1` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`), CRM `1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`), MariaDB `11.8`. Synthetic data only.

## Empty-volume bootstrap

A new Compose project (`codex-p0-clean-20261015`) was created with fresh database, Redis, and bench volumes. After assigning the empty bench volume to the `frappe` container user, the current `deploy/dev/bootstrap.sh` completed without reusing an existing bench or site.

- `list-apps`: Frappe `15.123.1`, CRM `1.87.0`, `crm_cskh 0.0.1`.
- SHA checks matched the pinned Frappe and CRM commits.
- Bootstrap ran the CRM frontend production build and migrated `p0-clean.localhost` successfully.
- The seed created synthetic data; the site migration passed again after seeding.
- A new `p0-test.localhost` installed CRM and `crm_cskh`, migrated, ran **28 Frappe tests (8.201s, OK)**, and migrated again successfully. Ruff `check` and `format --check` also passed.

This verifies a fresh Docker bench volume on this DEV host. It does not verify a second machine, MariaDB versions other than 11.8, or a GitHub Actions run.

## Backup and restore

In a separate Compose project (`codex-p0-smoke-20261015`), the seeded site was backed up and restored to a newly created `p0-restore.localhost` site.

- Restore completed with database, public files, and private files.
- `list-apps` after restore: Frappe `15.123.1`, CRM `1.87.0`, `crm_cskh 0.0.1`.
- Migrations succeeded after restore and on a second run.
- Restored counts: **15 CRM Leads, 3 CRM Call Logs, 2 CRM Tasks**.
- Private attachment `DEMO-CRM-LEAD-00001.txt` was present (90 bytes).
- `crm_cskh.api.health.ping` returned `status=ok` on the restored site.

The first restore attempt omitted the MariaDB root password and failed with access denied; the retry with the configured root password succeeded. No production or user data was used.

## Lead import and SLA probes

- Imported one synthetic CSV row through Frappe Data Import with only `first_name`, `mobile_no`, and `status`; the importer reported `1 of 1` imported. The saved Lead kept `09000008888` and `status=New`.
- Applied a minimal SLA to a synthetic Lead. Frappe set `response_by` one hour after creation and `sla_status=First Response Due`.

## Security and source reload

- The current app suite runs 28 tests, including 18 permission-boundary methods. The added field-scope regression test confirms `crm.api.doc.get_data` does not return a synthetic `permlevel=1` field to the owning Sales User. Owner/team scope, linked records, private files, export, reassignment denial, manager-without-hierarchy, and BOD least privilege checks also pass.
- A temporary change to the app health response appeared through the running `bench start` server as `0.0.1-hot-reload-probe`; the source file was restored and the next request returned `0.0.1`. No probe code remains.
- CSKH page `/cskh` was opened and reloaded in the browser as `staff.a@dev.local`; the page displayed Vietnamese labels and its authenticated POST/CSRF check returned `ok`. The screenshot was observed in the Codex browser session; it is not saved as a repository image.

## Limits

Frappe emitted its compatibility warning because MariaDB `11.8` is newer than `10.8`, the latest version this pinned Framework build reports as tested. The official v15 installation requirement is MariaDB `10.6.6+`; this exact `11.8` stack passed bootstrap, migrations, tests, import, SLA, backup, and restore, but upgrade and production workloads remain outside this evidence. Vue's Vite page rendered blank during the browser probe, so UI hot reload is not claimed.

## GitHub Actions pull request run

PR #1 passed on a clean GitHub-hosted runner: empty-volume bootstrap, Ruff lint/format, seed and repeated migration, fresh test-site installation, **28 Frappe tests**, and backup/restore with recovered-record/file/health assertions. The isolated stack was removed at the end. [Run details](https://github.com/nobita6986/new-crm/actions/runs/38712593755); see [PR checks](https://github.com/nobita6986/new-crm/pull/1/checks) for the current head.
