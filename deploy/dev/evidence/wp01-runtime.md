# WP-01 Runtime Evidence

> Date: 2026-10-15 · Scope: DEV host setup, baseline CRM install, seed, and HTTP/API smoke checks.
> This records a successful run on the current Windows host; it is not the P0 exit report and does not verify `crm_cskh` or security.

## Environment

- WSL reports default version 2; `docker-desktop` is running as a WSL 2 distribution.
- Docker Desktop Linux Engine: 29.7.2, `desktop-linux`, `linux/amd64`; Compose: 5.6.0.
- `docker compose config --quiet` passed. `db`, `redis-cache`, `redis-queue`, and `bench` started; MariaDB became healthy.
- Pulled image references match the locked digests: bench `sha256:1b27bb30…`, MariaDB `sha256:6422478c…`, Redis `sha256:ac2da09b…`.
- Inside the pinned bench image: Bench 5.31.0, Python 3.14.8, Node 24.22.0, Yarn 1.22.22.

## Install and seed

- `bootstrap.sh` created `dev.localhost`, installed `frappe` and `crm`, and migrated successfully.
- Installed revisions: Frappe `d2efa79657ba5c1abf96098b3bfacc199517d57e`; CRM `0d3910b456ef4fb913e77b29db011b3debc4f778`.
- `bench --site dev.localhost list-apps`: Frappe 15.123.1 and CRM 1.87.0.
- A second bootstrap and migrate succeeded on the existing site; a separate migrate also succeeded after the seed completed.
- `docker compose down` (without `-v`) followed by `up -d` recreated all containers; Frappe/CRM SHAs remained pinned, login/API worked, and `frappe.client.get_count` still returned 15 Leads.
- Seed succeeded with 7 fake users, 2 leader-rooted sales groups, 15 fake Leads, 3 Call Logs, 2 Tasks, one private fake attachment, and the demo A2 reassignment. A second seed run exited successfully without creating additional records.
- A4 and U1 exercise phone-only intake using the documented placeholder name; all phone numbers are non-dialable placeholders.

## HTTP/API

- Authenticated `GET /crm`: HTTP 200 (`text/html`). Login used the local DEV Administrator account.
- `POST /api/method/login`: `Logged In`; authenticated `GET /api/method/frappe.auth.get_logged_user`: `Administrator`.
- An unauthenticated `/crm` request returned 403, as the route requires login.
- `dev.localhost` does not resolve in the Windows host DNS, and this shell is not elevated to edit the hosts file. The HTTP checks used loopback with `Host: dev.localhost`; a browser on this host needs `127.0.0.1 dev.localhost` in the Windows hosts file or an equivalent local resolver entry.

## Caveat and remaining scope

- Frappe warned that MariaDB 11.8 is newer than 10.8 and has not yet been tested with this Framework version. Site creation, install, and migrations succeeded; that warning still needs an explicit compatibility decision before finalizing ADR-001.
- This run does not prove a second-machine clean setup, Vue hot reload or a source-edit/reload cycle, backup restore, `crm_cskh` installation, CI, i18n rendering, or any SEC-01…SEC-28 security case. P0 is not complete.
- Bootstrap now fetches only the locked upstream tag, and the seed wrapper streams framework logs. Seed fixtures match the upstream CRM Sales Hierarchy, Lost Reason, and Call Log schemas at the pinned revision.

Raw command output is kept locally under `evidence/*.log`; these files are excluded by the directory `.gitignore`.
